-- Walka road on the world map: the road a character has travelled, drawn
-- on the game's own world map - every zone in the game's full detail - and
-- on OpenMap's seamless map when that addon is installed.
--
--   +--------------------------------------------------------------------+
--   | [= Walka road]                                                     |
--   |                                                                    |
--   |      the world map, as the game (or OpenMap) shows it,             |
--   |      with the road over it: coloured by level, the stretches       |
--   |      walked on the treadmill bright, the rest dim                  |
--   |                                                                    |
--   +--------------------------------------------------------------------+
--
-- Where a point of the road goes
--   Blizzard's map   the map on show says where three of its places are in
--                    the world (C_Map.GetWorldPosFromMapPos), which gives
--                    the way from world yards to a place on it. On the map
--                    of the whole world each continent's own map is placed
--                    with C_Map.GetMapRectOnMap.
--   OpenMap's map    while its stitched map is on show, OpenMap's interface
--                    for other addons says where a world position is
--                    (OpenMapAPI.CanvasFromWorldPos, docs/api.md there).
--                    Nothing of OpenMap's is copied or changed: Walka calls
--                    that interface, and only when it is there.
--
-- Staying out of the map's way
--   The road is Line regions on one frame of Walka's own, a child of the
--   map's canvas: it moves and zooms with the map by itself. Walka writes
--   nothing to the world map, puts no code inside its refresh (no data
--   provider, no pins) and takes no mouse input on the map itself: on this
--   client an addon whose code runs inside the map's own makes the game
--   block parts of the map in combat. Instead Walka looks at the view once
--   a frame while the map is open, and draws again when it has come to
--   rest. Its one hook is a secure one that only notes that the map shows
--   another map. Walka never opens the map in combat, and makes its frames
--   and sets their levels out of combat.
--
-- The lines themselves are worked out by Road.lines (Core/Road.lua).

local ADDON_NAME, ns = ...

local Walka = ns.Walka
local Widgets = ns.Widgets
local Road = ns.Road
local RoadRecorder = ns.RoadRecorder
local RoadMap = ns.RoadMap
local COLOR = Widgets.COLOR

local WorldMapRoad = {}
ns.WorldMapRoad = WorldMapRoad

local floor, max, min, abs, huge = math.floor, math.max, math.min, math.abs, math.huge

-- The layer of the world map the road lies just above: the art of the
-- places the character has explored. Everything the map marks (icons, the
-- player's arrow) is above that.
local EXPLORED_LEVEL = "PIN_FRAME_LEVEL_MAP_EXPLORATION"
local EXPLORED_LEVEL_GUESS = 2000   -- where that layer is, should the map not say
local BUTTON_LEVELS_UP = 8700       -- the button: above the map and what it marks, under what is laid
local BUTTON_HIGHEST_LEVEL = 9700   -- ... over the whole map (OpenMap's dungeon view, 8800 up from the same)
local HIGHEST_LEVEL = 9900

local MAX_PIECES = 3000         -- pieces of road at once ...
local FULL_LOOK_PIECES = 1500   -- ... each with its dark edge and glow when there are no more than this
local TOLERANCE = 0.6           -- how far off a line may be, in screen units
local MARGIN = 0.3              -- of the view, drawn beyond each of its edges
local SETTLE_SECONDS = 0.15     -- the view must be still this long before the road is drawn again ...
local MAX_WAIT_SECONDS = 0.5    -- ... but while it keeps moving, at least this often
local ROAD_SECONDS = 0.5        -- how often a growing road is read and drawn again
local RETRY_SECONDS = 2         -- how often a map that could not be placed is asked about again
local WARM_SECONDS = 0.1        -- after login the road is made ready to draw, a little at a time:
local WARM_MILLISECONDS = 3     -- ... this long, this often
local PROBE_YARDS = 500

local CASING = { 0, 0, 0 }
local LEVEL_BANDS = { "1-9", "10-19", "20-29", "30-39", "40-49", "50-60" }
local BUTTON_POINT = { 8, -8 }  -- from the top-left corner of the map view (OpenMap's own controls are elsewhere)
local WATCH_SECONDS = 0.2       -- how often the button asks whether this map can show a road at all

local map, container, canvas    -- the world map, what it is seen through, and the map itself
local overlay, chip
local lines, widths = {}, {}
local lineCount = 0             -- lines of the road on show ...
local tailCount = 0             -- ... and after them, those of its end (see placeTail)
local tailTo = {}
local tailOwn = {}              -- the tail's own place, on a map that is not one straight picture
local readEnd = {}              -- where the stretch being recorded ended when the road was last read
local pieces = {}

local view = {}                 -- the map's view, as last looked at
local drawn = {}                -- ... and as the lines on show were drawn for
local seen = {}                 -- ... and at the look before this one
local redraw = true             -- draw at the next look, whatever the view
local restAt, movedAt = nil, nil
local roadClock, retryClock = 0, 0

local chapters = nil            -- the road as read (Road.chapters)
local roadYards = -1
local roadOn = {}               -- the continents the road is on
local roadContinents = ""
local places, placesFor = nil, nil  -- continent -> { ux, uy, u0, vx, vy, v0 } for the map on show
local scaled = {}               -- the same in the canvas's units
local fits = setmetatable({}, { __mode = "k" })     -- chapter -> its own place (a map that is not straight)
local skipped = 0
local plainPlaces = {}          -- map id -> places on Blizzard's map
local continents = nil

local openMap = nil             -- OpenMapAPI, once found
local openMapProblem = nil
local toggleRegistered, toggleState = false, nil
local toggleFromOpenMap = false
local worldMapProblem = "not looked for yet"
local quietUntil = 0            -- after an error of Walka's own here: no drawing until then

----------------------------------------------------------------------------
-- Small helpers
----------------------------------------------------------------------------

local function isSecret(value)
    return issecretvalue ~= nil and issecretvalue(value)
end

-- A number that arithmetic can be done on, or nil.
local function plain(value)
    if isSecret(value) or type(value) ~= "number" or value ~= value or value == huge or value == -huge then
        return nil
    end
    return value
end

local function inCombat()
    return InCombatLockdown ~= nil and InCombatLockdown() and true or false
end

local function roadShown()
    return Walka:Get("worldMap.road") ~= false
end

----------------------------------------------------------------------------
-- OpenMap
----------------------------------------------------------------------------

local function findOpenMap()
    if openMap then
        return openMap
    end
    local api = OpenMapAPI
    if type(api) ~= "table" then
        return nil
    end
    if api.major ~= 1 or type(api.version) ~= "number" then
        openMapProblem = "OpenMap's interface for other addons is not the one Walka knows (major version "
            .. tostring(api.major) .. ")"
        return nil
    end
    openMap = api
    return api
end

-- Call OpenMap. Whatever it does, an error of its own stays its own.
local function ask(name, ...)
    local fn = openMap and openMap[name]
    if type(fn) ~= "function" then
        return nil
    end
    local ok, first, second = pcall(fn, ...)
    if not ok then
        openMapProblem = "OpenMapAPI." .. name .. " raised: " .. tostring(first)
        return nil
    end
    return first, second
end

local function isStitched()
    return openMap ~= nil and ask("IsStitched") == true
end

-- A switch for the road in OpenMap's own menu (which can be starred onto
-- its map). OpenMap asks `get` whenever it draws the switch; registering
-- again is how it is told that the answer changed.
local function registerToggle()
    local api = findOpenMap()
    if not api or type(api.RegisterMenuToggle) ~= "function" then
        return
    end
    local ok, problem = pcall(api.RegisterMenuToggle, "Walka", {
        id = "road",
        label = "My road (Walka)",
        get = roadShown,
        set = function(on)
            toggleFromOpenMap = true
            WorldMapRoad.SetShown(on and true or false)
            toggleFromOpenMap = false
        end,
        icon = "Interface\\Icons\\Ability_Rogue_Sprint",
        tab = "show",
        section = "Walka",
    })
    toggleRegistered = ok and true or false
    toggleState = roadShown()
    if not ok then
        openMapProblem = "OpenMapAPI.RegisterMenuToggle raised: " .. tostring(problem)
    end
end

----------------------------------------------------------------------------
-- Where the road goes on the map on show
----------------------------------------------------------------------------

local function continentMaps()
    if not continents or #continents == 0 then
        continents = RoadMap.FindMaps()
    end
    return continents
end

-- Blizzard's map: the places of the continents on map `mapId`, or nil if
-- that map shows none of the world (a dungeon's map).
local function blizzardPlaces(mapId)
    local known = plainPlaces[mapId]
    if known then
        return known
    end
    local set, any = {}, false
    local okInfo, info = pcall(C_Map.GetMapInfo, mapId)
    local mapType = okInfo and type(info) == "table" and info.mapType or nil
    local kinds = Enum and Enum.UIMapType
    if mapType ~= nil and kinds and (mapType == kinds.World or mapType == kinds.Cosmic) and C_Map.GetMapRectOnMap then
        -- A map of more than one continent: each continent's own map says
        -- where the world is on it, and the game where that map lies on this.
        for _, continent in ipairs(continentMaps()) do
            local ok, minX, maxX, minY, maxY = pcall(C_Map.GetMapRectOnMap, continent.mapId, mapId)
            minX, maxX, minY, maxY = plain(minX), plain(maxX), plain(minY), plain(maxY)
            if ok and minX and maxX and minY and maxY and maxX - minX > 1e-6 and maxY - minY > 1e-6 then
                local unit, width, height = continent.unit, maxX - minX, maxY - minY
                set[continent.continent] = {
                    unit[1] * width, unit[2] * width, minX + unit[3] * width,
                    unit[4] * height, unit[5] * height, minY + unit[6] * height,
                }
                any = true
            end
        end
    end
    if not any then
        local ok, described = pcall(RoadMap.Describe, mapId)
        if ok and described then
            set[described.continent] = described.unit
            any = true
        end
    end
    if not any then
        return nil      -- (not remembered: early in a session the game may not answer yet)
    end
    plainPlaces[mapId] = set
    return set
end

-- OpenMap's map: where it puts three places near (x, y), as a place.
local function fit(continent, x, y)
    local u0, v0 = ask("CanvasFromWorldPos", continent, x, y)
    local u1, v1 = ask("CanvasFromWorldPos", continent, x + PROBE_YARDS, y)
    local u2, v2 = ask("CanvasFromWorldPos", continent, x, y + PROBE_YARDS)
    u0, v0, u1, v1, u2, v2 = plain(u0), plain(v0), plain(u1), plain(v1), plain(u2), plain(v2)
    if not (u0 and v0 and u1 and v1 and u2 and v2) then
        return nil
    end
    local ux, uy = (u1 - u0) / PROBE_YARDS, (u2 - u0) / PROBE_YARDS
    local vx, vy = (v1 - v0) / PROBE_YARDS, (v2 - v0) / PROBE_YARDS
    return { ux, uy, u0 - ux * x - uy * y, vx, vy, v0 - vx * x - vy * y }
end

-- Does OpenMap put the point (x, y) where `place` says?
local function agrees(place, continent, x, y)
    local u, v = ask("CanvasFromWorldPos", continent, x, y)
    u, v = plain(u), plain(v)
    return u ~= nil and v ~= nil
        and abs(u - (place[3] + place[1] * x + place[2] * y)) < 1e-4
        and abs(v - (place[6] + place[4] * x + place[5] * y)) < 1e-4
end

-- OpenMap's map. On this client its picture of a continent is straight, so
-- three places give all the others; that is checked against places along
-- the road, and a continent that fails the check is placed chapter by
-- chapter instead (placeOf).
local function stitchedPlaces()
    if not (openMap.version >= 2 and type(openMap.CanvasFromWorldPos) == "function") then
        openMapProblem = "this OpenMap is too old to tell other addons where things are on its map"
        return nil
    end
    local byContinent = {}
    for _, chapter in ipairs(chapters) do
        local list = byContinent[chapter.c]
        if not list then
            list = {}
            byContinent[chapter.c] = list
        end
        list[#list + 1] = chapter
    end
    local set, any = {}, false
    -- Where the character is now, the road is about to be: that continent
    -- is placed although no road is stored on it yet (for the road's end).
    local here, hereX, hereY = RoadRecorder.Tail()
    if here ~= nil and not byContinent[here] then
        local place = fit(here, hereX, hereY)
        if place then
            place.straight = true
            set[here] = place
            any = true
        end
    end
    for continent, list in pairs(byContinent) do
        local place = fit(continent, list[1].startX, list[1].startY)
        if place then
            place.straight = true
            local step = max(1, floor(#list / 6))
            for index = 1, #list, step do
                local chapter = list[index]
                if not agrees(place, continent, chapter.endX, chapter.endY) then
                    place.straight = false
                    break
                end
            end
            set[continent] = place
            any = true
        end
    end
    return any and set or nil
end

-- For Road.lines: how world yards become canvas units for a chapter.
local function placeOf(chapter)
    local place = scaled[chapter.c]
    if not place or place.straight then
        return place
    end
    local own = fits[chapter]
    if own == nil then
        own = false
        local fitted = fit(chapter.c, chapter.startX, chapter.startY)
        if fitted and agrees(fitted, chapter.c, chapter.endX, chapter.endY) then
            local width, height = drawn.width, drawn.height
            own = {
                fitted[1] * width, fitted[2] * width, fitted[3] * width,
                fitted[4] * height, fitted[5] * height, fitted[6] * height,
            }
        end
        fits[chapter] = own
    end
    if not own then
        skipped = skipped + 1       -- (counted afresh at every drawing)
    end
    return own or nil
end

----------------------------------------------------------------------------
-- The road
----------------------------------------------------------------------------

-- Where the stretch being recorded ends, as far as the road is drawn.
local function noteEnd()
    local _
    _, readEnd.x, readEnd.y, _, _, _, _, _, readEnd.segment = RoadRecorder.Tail()
end

-- The continents the road is on, and the one the character is on. When
-- that changes, the map is asked afresh where they lie on it.
local function noteContinents()
    local names = {}
    for continent in pairs(roadOn) do
        names[#names + 1] = tostring(continent)
    end
    local here = RoadRecorder.Tail()
    if here ~= nil and not roadOn[here] then
        names[#names + 1] = tostring(here)
    end
    table.sort(names)
    local key = table.concat(names, ",")
    if key ~= roadContinents then
        roadContinents = key
        placesFor = nil
        redraw = true
    end
end

local function readRoad()
    RoadRecorder.Flush()
    local db = RoadRecorder.GetDatabase()
    chapters = Road.chapters(db)
    roadYards = db and db.yards or 0
    noteEnd()
    for continent in pairs(roadOn) do
        roadOn[continent] = nil
    end
    for _, chapter in ipairs(chapters) do
        roadOn[chapter.c] = true
    end
    noteContinents()
end

-- How a stretch travelled in `mode` is drawn: colour, opacity, width,
-- width of the dark line under it, width of the glow.
local function look(mode, belt, level)
    if mode == "t" then
        return RoadMap.FLIGHT_COLOR, 0.6, 1.2, 0, 0
    elseif mode == "g" then
        return RoadMap.GHOST_COLOR, 0.65, 1.6, 3.2, 0
    elseif belt then
        return RoadMap.LevelColor(level), 1, 2.6, 4.6, 8
    end
    return RoadMap.LevelColor(level), 0.8, 1.8, 3.6, 0
end

----------------------------------------------------------------------------
-- Looking at the map
----------------------------------------------------------------------------

-- The frame level for the road: just above the explored places' art and,
-- with OpenMap, above all of its map art; under what the map marks. (No
-- level is free between OpenMap's art and the lowest icons it lifts above
-- it - Questie's townsfolk, Zygor's waypoint: those share the road's level,
-- and which of the two is drawn over the other there is the game's choice.)
local function roadLevel()
    local level
    if type(map.GetPinFrameLevelsManager) == "function" then
        local ok, manager = pcall(map.GetPinFrameLevelsManager, map)
        if ok and type(manager) == "table" and type(manager.GetValidFrameLevel) == "function" then
            local okLevel, value = pcall(manager.GetValidFrameLevel, manager, EXPLORED_LEVEL)
            level = okLevel and plain(value) or nil
        end
    end
    level = (level or EXPLORED_LEVEL_GUESS) + 1
    local top = openMap and plain(ask("ArtTopLevel"))
    if top then
        level = max(level, top + 1)
    end
    return min(HIGHEST_LEVEL, floor(level))
end

-- Fill `view`: the map on show and the part of its canvas in sight, in the
-- canvas's own units. False if the map cannot be measured right now.
local function measure()
    local scale = plain(canvas:GetEffectiveScale())
    local width, height = plain(canvas:GetWidth()), plain(canvas:GetHeight())
    local left, top = plain(canvas:GetLeft()), plain(canvas:GetTop())
    if not (scale and scale > 0 and width and width > 0 and height and height > 0 and left and top) then
        return false
    end
    local x0, y0, x1, y1 = 0, 0, width, height
    local boxScale = plain(container:GetEffectiveScale())
    local boxLeft, boxRight = plain(container:GetLeft()), plain(container:GetRight())
    local boxTop, boxBottom = plain(container:GetTop()), plain(container:GetBottom())
    if boxScale and boxLeft and boxRight and boxTop and boxBottom then
        local ratio = boxScale / scale
        x0, x1 = boxLeft * ratio - left, boxRight * ratio - left
        y0, y1 = top - boxTop * ratio, top - boxBottom * ratio
    end
    local okMap, mapId = pcall(map.GetMapID, map)
    view.mapId = okMap and plain(mapId) or nil
    view.stitched = isStitched()
    -- (A view laid over the map with the map's own picture faded out - OpenMap's
    -- view for controllers does that - takes the road with it.)
    view.unseen = type(canvas.GetEffectiveAlpha) == "function" and plain(canvas:GetEffectiveAlpha()) == 0
    view.scale, view.width, view.height = scale, width, height
    view.x0, view.y0, view.x1, view.y1 = x0, y0, x1, y1
    return true
end

-- Canvas units to one screen unit (the size the rest of the interface is
-- drawn in): what a line's width is multiplied by to look the same at any
-- zoom of the map.
local function unitOf(scale)
    local ui = plain(UIParent:GetEffectiveScale()) or 1
    return ui / scale
end

local function put(index, x1, y1, x2, y2, color, alpha, width, sublevel, unit)
    local item = lines[index]
    if not item then
        item = overlay:CreateLine(nil, "ARTWORK")
        lines[index] = item
    end
    item:SetDrawLayer("ARTWORK", sublevel)
    item:SetColorTexture(color[1], color[2], color[3], alpha)
    item:SetThickness(width * unit)
    item:SetStartPoint("TOPLEFT", overlay, x1, -y1)
    item:SetEndPoint("TOPLEFT", overlay, x2, -y2)
    item:Show()
    widths[index] = width
end

-- One piece of road from (x1, y1) to (x2, y2), from line `index` on: the
-- dark line under it and the glow (unless `plainly`), and the line itself.
-- Returns the next free index.
local function putPiece(index, x1, y1, x2, y2, mode, belt, level, sublevel, unit, plainly)
    local color, alpha, width, casing, glow = look(mode, belt, level)
    if casing > 0 and not plainly then
        put(index, x1, y1, x2, y2, CASING, 0.55, casing, sublevel, unit)
        index = index + 1
    end
    if glow > 0 and not plainly then
        put(index, x1, y1, x2, y2, color, 0.3, glow, sublevel + 1, unit)
        index = index + 1
    end
    put(index, x1, y1, x2, y2, color, alpha, width, sublevel + 2, unit)
    return index + 1
end

-- The end of the road. The recorder stores a point every twenty yards or
-- so, and the road is read again twice a second at most; from where the
-- drawn road ends to where the character stands it is drawn here, so that
-- it reaches the map's own arrow. Looked at every frame, touched only when
-- the character has moved.
local function placeTail(force)
    local count = 0
    local continent, fromX, fromY, toX, toY, mode, belt, level, segment = RoadRecorder.Tail()
    if segment ~= nil and segment == readEnd.segment then
        fromX, fromY = readEnd.x, readEnd.y     -- (points stored since the last reading are not drawn yet)
    end
    local place = continent and places and scaled[continent]
    if place and not place.straight then
        -- (OpenMap's map of a continent that is not one straight picture:
        -- the end of the road is placed by where OpenMap puts its start.)
        if tailOwn.c ~= continent or tailOwn.x ~= fromX or tailOwn.y ~= fromY or tailOwn.key ~= placesFor then
            tailOwn.c, tailOwn.x, tailOwn.y, tailOwn.key = continent, fromX, fromY, placesFor
            local fitted = fit(continent, fromX, fromY)
            local width, height = drawn.width, drawn.height
            tailOwn.place = fitted and {
                fitted[1] * width, fitted[2] * width, fitted[3] * width,
                fitted[4] * height, fitted[5] * height, fitted[6] * height,
            } or nil
        end
        place = tailOwn.place
    end
    if place and (fromX ~= toX or fromY ~= toY) then
        local to = tailTo
        if not force and to.x == toX and to.y == toY and to.fromX == fromX and to.fromY == fromY
            and to.mode == mode and to.belt == belt and to.level == level then
            return      -- as it is drawn
        end
        to.x, to.y, to.fromX, to.fromY, to.mode, to.belt, to.level = toX, toY, fromX, fromY, mode, belt, level
        local x1, y1 = place[3] + place[1] * fromX + place[2] * fromY, place[6] + place[4] * fromX + place[5] * fromY
        local x2, y2 = place[3] + place[1] * toX + place[2] * toY, place[6] + place[4] * toX + place[5] * toY
        -- (Above the road itself: draw layers 3 to 5, the road's are 0 to 2.)
        count = putPiece(lineCount + 1, x1, y1, x2, y2, mode, belt, level, 3, unitOf(drawn.thick), false)
            - lineCount - 1
    else
        tailTo.x = nil
        if tailCount == 0 and not force then
            return
        end
    end
    -- Lines no longer in use: those of a longer tail before this one or,
    -- after the road was drawn afresh, every line beyond it.
    for index = lineCount + count + 1, force and #lines or lineCount + tailCount do
        lines[index]:Hide()
    end
    tailCount = count
end

local box = {}

-- Draw the road for the view just measured.
local function draw()
    redraw, restAt, movedAt = false, nil, nil
    drawn.mapId, drawn.stitched = view.mapId, view.stitched
    drawn.width, drawn.height, drawn.scale, drawn.thick = view.width, view.height, view.scale, view.scale
    drawn.x0, drawn.y0, drawn.x1, drawn.y1 = view.x0, view.y0, view.x1, view.y1
    if not inCombat() then
        local level = roadLevel()
        if overlay:GetFrameLevel() ~= level then
            overlay:SetFrameLevel(level)
        end
    end
    if not chapters then
        readRoad()
    end

    local key = tostring(view.mapId) .. (view.stitched and "s" or "b") .. view.width .. "x" .. view.height
    if key ~= placesFor then
        placesFor = key
        skipped = 0
        for stretch in pairs(fits) do
            fits[stretch] = nil
        end
        if view.stitched then
            places = stitchedPlaces()
        elseif view.mapId then
            places = blizzardPlaces(view.mapId)
        else
            places = nil
        end
        for continent in pairs(scaled) do
            scaled[continent] = nil
        end
        for continent, place in pairs(places or {}) do
            local width, height = view.width, view.height
            scaled[continent] = {
                place[1] * width, place[2] * width, place[3] * width,
                place[4] * height, place[5] * height, place[6] * height,
                straight = place.straight ~= false,
            }
        end
    end

    lineCount = 0
    pieces.count, pieces.cut, pieces.passes = 0, 0, 0
    if places then
        local unit = unitOf(view.scale)
        local marginX, marginY = (view.x1 - view.x0) * MARGIN, (view.y1 - view.y0) * MARGIN
        box.left, box.right = view.x0 - marginX, view.x1 + marginX
        box.top, box.bottom = view.y0 - marginY, view.y1 + marginY
        if not view.stitched then
            -- Blizzard's map ends at its edges (OpenMap's shows the country around).
            box.left, box.right = max(box.left, 0), min(box.right, view.width)
            box.top, box.bottom = max(box.top, 0), min(box.bottom, view.height)
        end
        box.tolerance = TOLERANCE * unit
        box.most = MAX_PIECES
        skipped = 0
        Road.lines(chapters, placeOf, box, pieces)
        -- A map with the road of many levels on it at once is drawn in
        -- lines only: edged and glowing they would be one dark smear (and
        -- three times the lines).
        local plainly = pieces.count > FULL_LOOK_PIECES
        local nextLine = 1
        for index = 1, pieces.count do
            local from = pieces.from[index]
            nextLine = putPiece(nextLine, pieces.x1[index], pieces.y1[index], pieces.x2[index], pieces.y2[index],
                from.mode, from.belt, from.level, 0, unit, plainly)
        end
        lineCount = nextLine - 1
    end
    tailCount = 0
    placeTail(true)
    overlay:SetAlpha(1)     -- (hidden since the map changed, see attach)
end

-- The map zoomed: the lines keep their width on screen.
local function fitWidths()
    local unit = unitOf(view.scale)
    for index = 1, lineCount + tailCount do
        lines[index]:SetThickness(widths[index] * unit)
    end
    drawn.thick = view.scale
end

local function differs(a, b, slack)
    return abs(a.x0 - b.x0) > slack or abs(a.y0 - b.y0) > slack or abs(a.x1 - b.x1) > slack
        or abs(a.y1 - b.y1) > slack or abs(a.scale - b.scale) > 1e-4 * b.scale
end

local function step(elapsed)
    if not measure() then
        return
    end
    if redraw or view.mapId ~= drawn.mapId or view.stitched ~= drawn.stitched
        or view.width ~= drawn.width or view.height ~= drawn.height then
        -- Another map under the lines: they are in the wrong places.
        draw()
    else
        if abs(view.scale - drawn.thick) > 1e-4 * drawn.thick then
            fitWidths()
        end
        local slack = 0.25 * unitOf(view.scale)
        if differs(view, drawn, slack) then
            local now = GetTime()
            if not movedAt then
                movedAt, restAt = now, now + SETTLE_SECONDS
            elseif differs(view, seen, slack) then
                restAt = now + SETTLE_SECONDS
            end
            if now >= restAt or now - movedAt >= MAX_WAIT_SECONDS then
                draw()
            end
        end
    end
    seen.x0, seen.y0, seen.x1, seen.y1, seen.scale = view.x0, view.y0, view.x1, view.y1, view.scale

    -- The road grows as the character moves. (Read at once when another
    -- stretch is being recorded than at the last reading: the one before
    -- has just been ended, and its end is not drawn by the tail any more.)
    elapsed = elapsed or 0
    roadClock = roadClock + elapsed
    if select(9, RoadRecorder.Tail()) ~= readEnd.segment then
        roadClock = ROAD_SECONDS
    end
    if roadClock >= ROAD_SECONDS then
        roadClock = 0
        local db = RoadRecorder.GetDatabase()
        if db and db.yards ~= roadYards then
            readRoad()
            redraw = true
        else
            noteEnd()
            noteContinents()
        end
    end
    if not redraw then
        placeTail(false)
    end
    -- A map that could not be placed (early in a session the game may not
    -- answer yet, and OpenMap may have had an error): asked again.
    retryClock = retryClock + elapsed
    if retryClock >= RETRY_SECONDS then
        retryClock = 0
        if not places then
            placesFor = nil
            redraw = true
        end
    end
end

-- Once a frame while the world map is open. An error of Walka's own must
-- not come back sixty times a second: it is remembered for /walka mapcheck,
-- the lines are taken away, and drawing rests for a few seconds.
local function onUpdate(_, elapsed)
    if GetTime() < quietUntil then
        return
    end
    local ok, problem = pcall(step, elapsed)
    if not ok then
        WorldMapRoad.problem = tostring(problem)
        quietUntil = GetTime() + 5
        redraw = true
        for index = 1, #lines do
            lines[index]:Hide()
        end
        lineCount, tailCount = 0, 0
    end
end

----------------------------------------------------------------------------
-- The button on the map
----------------------------------------------------------------------------

local function buttonText()
    local db = RoadRecorder.GetDatabase()
    local yards = db and db.yards or 0
    local body = {}
    if yards < 1 then
        body[#body + 1] = "No road recorded yet. Walka keeps your character's path from now on."
    else
        local share = floor((db.beltYards or 0) / yards * 100 + 0.5)
        body[#body + 1] = string.format("%s of road recorded, %d%% of it walked on the treadmill.",
            Walka:FormatDistance(yards * 0.9144), share)
    end
    local bands = {}
    for index, color in ipairs(RoadMap.LEVEL_COLORS) do
        bands[index] = Widgets.Colorize(LEVEL_BANDS[index], color)
    end
    body[#body + 1] = ""
    body[#body + 1] = "Colour is your level: " .. table.concat(bands, "  ")
    body[#body + 1] = "Bright with a glow: walked on the treadmill. Dimmer: the belt was off. "
        .. "Thin and pale: flight paths."
    if view.unseen and overlay and overlay:IsShown() then
        body[#body + 1] = "This view of the map hides the map's own picture, and the road with it."
    elseif view.stitched and overlay and overlay:IsShown() then
        body[#body + 1] = "Drawn on OpenMap's map."
    end
    if skipped > 0 then
        body[#body + 1] = string.format("%d stretches could not be placed on this map.", skipped)
    end
    body[#body + 1] = ""
    body[#body + 1] = roadShown() and "Click to hide the road." or "Click to show the road."
    return body
end

-- Can the map on show have a road on it at all? (A dungeon's map cannot;
-- there the corner is left to what the game puts in it.)
local function mapTakesRoad()
    if isStitched() then
        return true
    end
    local ok, mapId = pcall(map.GetMapID, map)
    mapId = ok and plain(mapId) or nil
    return mapId ~= nil and blizzardPlaces(mapId) ~= nil
end

local function refreshButton()
    if not chip then
        return
    end
    local on = roadShown()
    local shade = on and COLOR.white or COLOR.grey
    chip.label:SetTextColor(shade[1], shade[2], shade[3])
    for _, dash in ipairs(chip.dashes) do
        dash:SetAlpha(on and 1 or 0.35)
    end
    chip:SetShown(Walka:Get("worldMap.button") ~= false and mapTakesRoad())
end

local function buildButton()
    chip = CreateFrame("Button", "WalkaWorldMapRoadButton", map)
    chip:SetSize(96, 20)
    chip:SetPoint("TOPLEFT", container, "TOPLEFT", BUTTON_POINT[1], BUTTON_POINT[2])
    chip:SetFrameLevel(min(BUTTON_HIGHEST_LEVEL, container:GetFrameLevel() + BUTTON_LEVELS_UP))
    local back = Widgets.Fill(chip, "BACKGROUND", 0.03, 0.04, 0.06, 0.78)
    back:SetAllPoints(chip)
    -- A little road: three dashes in the colours of the first levels.
    chip.dashes = {}
    for index = 1, 3 do
        local color = RoadMap.LEVEL_COLORS[index + 1]
        local dash = Widgets.Fill(chip, "ARTWORK", color[1], color[2], color[3], 1)
        dash:SetSize(6, 3)
        dash:SetPoint("LEFT", chip, "LEFT", 6 + (index - 1) * 7, 0)
        chip.dashes[index] = dash
    end
    chip.label = Widgets.Text(chip, "GameFontHighlightSmall", "Walka road", "LEFT")
    chip.label:SetPoint("LEFT", chip, "LEFT", 31, 0)
    chip:SetScript("OnClick", function(self)
        Widgets.ClickSound()
        WorldMapRoad.SetShown(not roadShown())
        if GameTooltip and GameTooltip:GetOwner() == self then
            Widgets.ShowTooltip(self, "Walka: your road", buttonText(), "ANCHOR_BOTTOMRIGHT")
        end
    end)
    Widgets.Tooltip(chip, "Walka: your road", buttonText, "ANCHOR_BOTTOMRIGHT")
    -- The button follows the map on show (the road's own frame cannot do
    -- this: it rests while the road is switched off).
    local watch = CreateFrame("Frame", nil, map)
    local waited = WATCH_SECONDS
    watch:SetScript("OnUpdate", function(_, elapsed)
        waited = waited + (elapsed or 0)
        if waited >= WATCH_SECONDS then
            waited = 0
            pcall(refreshButton)
        end
    end)
    refreshButton()
end

----------------------------------------------------------------------------
-- Finding the world map
----------------------------------------------------------------------------

-- The world map and its canvas, or nil and why not.
local function findWorldMap()
    local frame = WorldMapFrame
    if frame == nil then
        return nil, "this client has not made its world map yet"
    end
    if type(frame) ~= "table" or type(frame.GetCanvas) ~= "function" or type(frame.GetMapID) ~= "function" then
        return nil, "the world map is not the kind Walka knows how to draw on"
    end
    local ok, child = pcall(frame.GetCanvas, frame)
    if not ok or type(child) ~= "table" or type(child.CreateLine) ~= "function"
        or type(child.GetEffectiveScale) ~= "function" then
        return nil, "the world map has no canvas to draw on"
    end
    return frame, child
end

local function applyShown()
    if not overlay then
        return
    end
    local on = roadShown()
    if on and not overlay:IsShown() then
        redraw = true
        chapters = nil      -- (read again: the road went on while it was not shown)
    end
    overlay:SetShown(on)
    refreshButton()
end

-- Put Walka's frames on the world map, if it is there. True once done.
local function attach()
    if overlay then
        return true
    end
    if inCombat() then
        worldMapProblem = "waiting for combat to end before touching the world map"
        return false
    end
    local frame, child = findWorldMap()
    if not frame then
        worldMapProblem = child
        return false
    end
    map, canvas = frame, child
    local scroll = frame.ScrollContainer
    if type(scroll) == "table" and type(scroll.GetLeft) == "function" then
        container = scroll
    else
        container = child:GetParent() or frame
    end
    findOpenMap()
    overlay = CreateFrame("Frame", "WalkaWorldMapRoad", canvas)
    -- At its level at once: a new frame starts just above its parent, which
    -- is under the map's art (and where another addon's frames are looked for).
    overlay:SetFrameLevel(roadLevel())
    overlay:SetAllPoints(canvas)
    overlay:SetScript("OnUpdate", onUpdate)
    overlay:SetScript("OnShow", function()
        roadClock = ROAD_SECONDS    -- the road may have grown while the map was closed
    end)
    -- Told at once when the map shows another map: the lines are in the
    -- wrong places until they are drawn for it, at the next frame.
    if hooksecurefunc and type(frame.OnMapChanged) == "function" then
        pcall(hooksecurefunc, frame, "OnMapChanged", function()
            redraw = true
            overlay:SetAlpha(0)
        end)
    end
    buildButton()
    applyShown()
    worldMapProblem = nil
    return true
end

----------------------------------------------------------------------------
-- Making the road ready ahead of time
----------------------------------------------------------------------------
-- Reading a long road and thinning it for every zoom takes a moment the
-- first time. Rather than when the map opens, it is done after login, a
-- few milliseconds at a time, and never in combat.

local warmTicker, warmAt = nil, 1

local function warm()
    if inCombat() then
        return
    end
    local clock = debugprofilestop
    local started = clock and clock() or 0
    local function expired()
        return clock ~= nil and clock() - started >= WARM_MILLISECONDS
    end
    local read, whole = Road.chapters(RoadRecorder.GetDatabase(), expired)
    if not whole then
        return
    end
    while warmAt <= #read do
        Road.prepare(read[warmAt])
        warmAt = warmAt + 1
        if expired() then
            return
        end
    end
    if warmTicker then
        warmTicker:Cancel()
        warmTicker = nil
    end
end

----------------------------------------------------------------------------
-- Public API
----------------------------------------------------------------------------

--- Is the road drawn on the world map (the setting)?
function WorldMapRoad.IsShown()
    return roadShown()
end

--- Show or hide the road on the world map.
function WorldMapRoad.SetShown(on)
    Walka:Set("worldMap.road", on and true or false)
end

--- Open the world map with the road on it. Returns true if the world map
-- is open (or was opened); false if it is not - this client has no world
-- map Walka can draw on, or it is closed during combat, when addon code
-- must not open it. The caller then has Walka's own road window.
function WorldMapRoad.Open()
    if not Walka.loaded then
        return false
    end
    local fighting = inCombat()
    if not attach() and not fighting and WorldMapFrame == nil and ToggleWorldMap then
        -- The game may make its world map only when it is first opened.
        pcall(ToggleWorldMap)
        attach()
    end
    if not roadShown() then
        WorldMapRoad.SetShown(true)
    end
    if overlay and map:IsShown() then
        return true
    end
    if fighting then
        Walka:Print("in combat Walka cannot open the world map for you. Open it yourself and your road is on it; "
            .. "this is Walka's own road window.")
        return false
    end
    if not overlay then
        return false
    end
    if ToggleWorldMap then
        pcall(ToggleWorldMap)
    end
    if not map:IsShown() then
        Walka:Print("your road is on the world map: open the map to see it.")
    end
    return true
end

--- What this module knows, for /walka mapcheck.
function WorldMapRoad.Check(print)
    attach()
    if not overlay then
        print("  world map: Walka is not on it - " .. tostring(worldMapProblem) .. ".")
    else
        local shownMap = "closed"
        if map:IsShown() and measure() then
            shownMap = string.format("showing map %s, %s", tostring(view.mapId),
                view.stitched and "OpenMap's stitched map" or "Blizzard's map")
        end
        print(string.format("  world map: road %s, %s; frame level %d.", roadShown() and "on" or "off", shownMap,
            overlay:GetFrameLevel()))
        if map:IsShown() and view.unseen then
            print("  this view of the map hides the map's own picture (OpenMap's view for controllers does): "
                .. "the road cannot be seen on it.")
        end
        if map:IsShown() and roadShown() then
            local onMap = {}
            for continent in pairs(places or {}) do
                onMap[#onMap + 1] = tostring(continent)
            end
            table.sort(onMap)
            print(string.format("  drawn: %d stretches of line (%d lines), continents on this map: %s; %d left out, %d not placed.",
                pieces.count or 0, lineCount + tailCount, #onMap > 0 and table.concat(onMap, ", ") or "none",
                pieces.cut or 0, skipped))
        end
    end
    if openMap then
        print(string.format("  OpenMap: found (interface version %s)%s.", tostring(openMap.version),
            toggleRegistered and ", the road has a switch in its menu" or ""))
    elseif not openMapProblem then
        print("  OpenMap: not installed (the road is drawn on Blizzard's map).")
    end
    if openMapProblem then
        print("  OpenMap: " .. openMapProblem .. ".")
    end
    if WorldMapRoad.problem then
        print("  drawing the road ran into: " .. WorldMapRoad.problem)
    end
end

----------------------------------------------------------------------------
-- Start
----------------------------------------------------------------------------

local function settle()
    if not Walka.loaded then
        return
    end
    findOpenMap()
    if not toggleRegistered then
        registerToggle()
    end
    attach()
end

Walka:On("LOADED", function()
    settle()
    if C_Timer and C_Timer.NewTicker and not warmTicker then
        warmTicker = C_Timer.NewTicker(WARM_SECONDS, function()
            pcall(warm)
        end)
    end
end)

Walka:On("SETTINGS", function()
    applyShown()
    if toggleRegistered and toggleState ~= roadShown() then
        if toggleFromOpenMap then
            toggleState = roadShown()   -- OpenMap's own switch was clicked: it knows
        else
            registerToggle()
        end
    end
end)

-- The road has reached a land whose map was not known (one the game does
-- not list among its continents): where the lands lie on a map of the
-- world is asked afresh.
Walka:On("ROAD_MAPS", function()
    continents = nil
    for mapId in pairs(plainPlaces) do
        plainPlaces[mapId] = nil
    end
    placesFor = nil
    redraw = true
end)

-- The world map, and OpenMap, may come after Walka; and nothing of the map
-- is touched in combat.
local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("PLAYER_REGEN_ENABLED")
events:SetScript("OnEvent", function(_, event)
    settle()
    if event == "PLAYER_REGEN_ENABLED" and overlay then
        redraw = true       -- (the frame level may be waiting)
    end
end)

return WorldMapRoad
