-- Walka road map: the road a character has travelled, on the game's map,
-- in a window of Walka's own (/walka map window). The road is first of all
-- drawn on the game's world map (UI/WorldMapRoad.lua); this window is what
-- there is where that map cannot be found, and it has the screenshot button.
--
--   Walka  road - Kalimdor                [<] [>] [Me] [Fit] [Screenshot] [x]
--   +--------------------------------------------------------------------+
--   |                                                                    |
--   |            (the continent, zoomable, draggable)                    |
--   |            the road, coloured by level; the stretches walked       |
--   |            on the treadmill bright, the rest dim                   |
--   |                                                                    |
--   +--------------------------------------------------------------------+
--   level  1-9  10-19  20-29  30-39  40-49  50-60      bright = walked on the treadmill
--   Road recorded: 46.6 mi - 61% of it walked for real.
--
-- Mouse wheel zooms about the cursor, dragging moves the map.
--
-- Nothing is shipped for the picture: the map art is the game's own, asked
-- for by map id (C_Map.GetMapArtLayers / GetMapArtLayerTextures, drawn as
-- tiles by file id), and world yards are turned into map positions with
-- C_Map.GetWorldPosFromMapPos. That is how Road to Sixty draws its map in
-- this client (see THIRD-PARTY-NOTICES.md); the window itself is Walka's.
--
-- An ordinary (unprotected) frame: it can be opened and used in combat.
-- The window is built the first time it is opened.

local ADDON_NAME, ns = ...

local Walka = ns.Walka
local Widgets = ns.Widgets
local Road = ns.Road
local RoadRecorder = ns.RoadRecorder
local COLOR = Widgets.COLOR

local RoadMap = {}
ns.RoadMap = RoadMap

local floor, ceil, max, min, sqrt = math.floor, math.ceil, math.max, math.min, math.sqrt

local WIDTH, HEIGHT = 720, 548
local MARGIN = 12
local HEADER, FOOTER = 34, 46
local CANVAS_WIDTH, CANVAS_HEIGHT = WIDTH - 2 * MARGIN, HEIGHT - HEADER - FOOTER

local WORLD_MAP = 947
-- If the game does not list its continents, these are tried (Kalimdor and
-- the Eastern Kingdoms in this client). A land that is not a continent of
-- that world has its map remembered with the road (see findMaps).
local KNOWN_CONTINENT_MAPS = { 1414, 1415 }

local ZOOM_STEP = 1.4
local MAX_ZOOM = 12
local MAX_PIECES = 1250         -- stretches of line at once (one walked on the treadmill is two lines)
local REBUILD_DELAY = 0.15      -- after the last turn of the wheel
local DEFAULT_POINT = { "CENTER", "CENTER", 0, 20 }

-- One colour per ten levels (the last band takes 50 to 60).
RoadMap.LEVEL_COLORS = {
    { 0.86, 0.86, 0.80 },
    { 0.45, 0.85, 0.45 },
    { 0.40, 0.70, 1.00 },
    { 0.76, 0.52, 0.96 },
    { 1.00, 0.60, 0.25 },
    { 1.00, 0.85, 0.35 },
}
local FLIGHT_COLOR = { 0.62, 0.72, 0.82 }
local GHOST_COLOR = { 0.55, 0.55, 0.60 }
RoadMap.FLIGHT_COLOR, RoadMap.GHOST_COLOR = FLIGHT_COLOR, GHOST_COLOR

local frame, canvas, content, overlay
local parts = {}
local tiles, lines = {}, {}
local lineCount = 0

local maps = nil            -- { { mapId, name, continent, toMap, layer }, ... }
local shown = 1             -- index into maps
local zoom, fitZoom = 1, 1
local ox, oy = 0, 0         -- the content point at the canvas's top-left corner
local cache = nil           -- the road as read: { chapters = .., shown = how many lie on this continent }
local pieces = {}           -- what Road.lines made of it for the present view
local rebuildAt = nil       -- GetTime() at which the lines are to be drawn again
local dragging = nil

----------------------------------------------------------------------------
-- The game's maps
----------------------------------------------------------------------------

local function isSecret(value)
    return issecretvalue ~= nil and issecretvalue(value)
end

local function vector(x, y)
    if CreateVector2D then
        return CreateVector2D(x, y)
    end
    return { x = x, y = y }
end

-- World position of a point on a map: continent id, x, y - or nil.
local function worldAt(mapId, u, v)
    local continent, world = C_Map.GetWorldPosFromMapPos(mapId, vector(u, v))
    if isSecret(continent) or isSecret(world) or type(continent) ~= "number" or type(world) ~= "table" then
        return nil
    end
    local x, y = world.x, world.y
    if isSecret(x) or isSecret(y) or type(x) ~= "number" or type(y) ~= "number" then
        return nil
    end
    return continent, x, y
end

-- A map as this window needs it, or nil if the game cannot place it.
local function describe(mapId, name)
    local continent, x0, y0 = worldAt(mapId, 0, 0)
    local _, x1, y1 = worldAt(mapId, 0.5, 0)
    local _, x2, y2 = worldAt(mapId, 0, 0.5)
    if not continent or not x1 or not x2 then
        return nil
    end
    -- The map is a straight (affine) picture of the world: three places on
    -- it give the way back from world yards to a place on it. (Its top-left
    -- corner and the middles of two edges, not its far corners: on some
    -- maps the game is said to be less exact at the far edge.)
    local ax, ay, bx, by = (x1 - x0) * 2, (y1 - y0) * 2, (x2 - x0) * 2, (y2 - y0) * 2
    local det = ax * by - bx * ay
    if det == 0 then
        return nil
    end
    local layers = C_Map.GetMapArtLayers and C_Map.GetMapArtLayers(mapId)
    local layer = type(layers) == "table" and layers[1] or nil
    local width = layer and tonumber(layer.layerWidth) or 1002
    local height = layer and tonumber(layer.layerHeight) or 668
    return {
        mapId = mapId,
        name = type(name) == "string" and name or ("map " .. mapId),
        continent = continent,
        width = width, height = height,
        tileWidth = layer and tonumber(layer.tileWidth) or 256,
        tileHeight = layer and tonumber(layer.tileHeight) or 256,
        hasArt = layer ~= nil,
        -- world yards -> content units (x right, y down)
        toMap = function(x, y)
            local dx, dy = x - x0, y - y0
            return (dx * by - bx * dy) / det * width, (ax * dy - dx * ay) / det * height
        end,
        -- ... and the same as numbers, for Road.lines
        place = {
            by / det * width, -bx / det * width, (bx * y0 - by * x0) / det * width,
            -ay / det * height, ax / det * height, (ay * x0 - ax * y0) / det * height,
        },
        -- ... and from 0 to 1 across and down the map, as the game counts
        -- places on a map (for the road on the world map)
        unit = {
            by / det, -bx / det, (bx * y0 - by * x0) / det,
            -ay / det, ax / det, (ay * x0 - ax * y0) / det,
        },
    }
end
RoadMap.Describe = describe

local function findMaps()
    local found = {}
    local seen = {}
    local function add(mapId, name)
        if type(mapId) == "number" and not seen[mapId] then
            seen[mapId] = true
            local ok, map = pcall(describe, mapId, name)
            if ok and map then
                found[#found + 1] = map
            end
        end
    end
    local continentType = Enum and Enum.UIMapType and Enum.UIMapType.Continent
    local ok, children = pcall(C_Map.GetMapChildrenInfo, WORLD_MAP, continentType)
    if ok and type(children) == "table" then
        for _, child in ipairs(children) do
            if type(child) == "table" then
                add(child.mapID, child.name)
            end
        end
    end
    for _, mapId in ipairs(KNOWN_CONTINENT_MAPS) do
        local okInfo, info = pcall(C_Map.GetMapInfo, mapId)
        add(mapId, okInfo and type(info) == "table" and info.name or nil)
    end
    -- Lands the game does not list among the continents of its world (the
    -- Skyborne's Zephras Isle, in Skywall): the recorder remembers, for
    -- every land the character has stood on, which map shows it.
    local db = RoadRecorder.GetDatabase()
    local remembered = db and db.maps
    if type(remembered) == "table" then
        local covered, lands = {}, {}
        for _, map in ipairs(found) do
            covered[map.continent] = true
        end
        for continent, kept in pairs(remembered) do
            if not covered[continent] and type(continent) == "number" and type(kept) == "table" then
                lands[#lands + 1] = continent
            end
        end
        table.sort(lands)
        for _, continent in ipairs(lands) do
            local kept = remembered[continent]
            local okInfo, info = pcall(C_Map.GetMapInfo, kept.id)
            local name = okInfo and type(info) == "table" and not isSecret(info.name) and info.name or nil
            local before = #found
            add(kept.id, type(name) == "string" and name or kept.name)
            -- (A map that now shows another land - the game's maps were
            -- numbered anew - is not this land's map any more.)
            if #found > before and found[#found].continent ~= continent then
                found[#found] = nil
            end
        end
    end
    return found
end
RoadMap.FindMaps = findMaps

----------------------------------------------------------------------------
-- The road, ready to draw
----------------------------------------------------------------------------

local function levelColor(level)
    local colors = RoadMap.LEVEL_COLORS
    local band = floor((tonumber(level) or 1) / 10) + 1
    return colors[max(1, min(#colors, band))]
end
RoadMap.LevelColor = levelColor

-- Read the road once per opening (and when the continent changes).
local function readRoad()
    local map = maps and maps[shown]
    cache = { chapters = {}, shown = 0 }
    if not map then
        return
    end
    RoadRecorder.Flush()
    cache.chapters = Road.chapters(RoadRecorder.GetDatabase())
    for _, chapter in ipairs(cache.chapters) do
        if chapter.c == map.continent then
            cache.shown = cache.shown + 1
        end
    end
end

----------------------------------------------------------------------------
-- Drawing
----------------------------------------------------------------------------

local function line(index)
    local item = lines[index]
    if not item then
        item = content:CreateLine(nil, "ARTWORK")
        lines[index] = item
    end
    return item
end

local function drawLine(x1, y1, x2, y2, color, alpha, thickness, sublevel)
    lineCount = lineCount + 1
    local item = line(lineCount)
    item:SetDrawLayer("ARTWORK", sublevel)
    item:SetColorTexture(color[1], color[2], color[3], alpha)
    item:SetThickness(thickness)
    item:SetStartPoint("TOPLEFT", content, x1, -y1)
    item:SetEndPoint("TOPLEFT", content, x2, -y2)
    item:Show()
end

local view = {}

-- Draw the road for the present zoom and view. Stretches out of sight are
-- left out, and what is drawn is thinned to what the eye can tell apart
-- (Road.lines keeps the number of lines bounded however long the road).
local function drawRoad()
    rebuildAt = nil
    if not cache then
        readRoad()
    end
    local map = maps and maps[shown]
    lineCount = 0
    if map then
        local viewWidth, viewHeight = CANVAS_WIDTH / zoom, CANVAS_HEIGHT / zoom
        -- One view further in every direction, so that dragging shows road.
        view.left, view.right = ox - viewWidth, ox + 2 * viewWidth
        view.top, view.bottom = oy - viewHeight, oy + 2 * viewHeight
        view.tolerance = 0.6 / zoom
        view.most = MAX_PIECES
        local continent, place = map.continent, map.place
        Road.lines(cache.chapters, function(chapter)
            return chapter.c == continent and place or nil
        end, view, pieces)
        local thin = 1 / zoom
        for index = 1, pieces.count do
            local from = pieces.from[index]
            local x1, y1, x2, y2 = pieces.x1[index], pieces.y1[index], pieces.x2[index], pieces.y2[index]
            local color, alpha, thickness = levelColor(from.level), 0.55, 2 * thin
            if from.mode == "t" then
                color, alpha, thickness = FLIGHT_COLOR, 0.35, 1.2 * thin
            elseif from.mode == "g" then
                color, alpha = GHOST_COLOR, 0.45
            elseif from.belt then
                alpha, thickness = 1, 2.6 * thin
                -- A soft, wider line under the bright one.
                drawLine(x1, y1, x2, y2, color, 0.28, 7 * thin, 1)
            end
            drawLine(x1, y1, x2, y2, color, alpha, thickness, 2)
        end
    end
    for index = lineCount + 1, #lines do
        lines[index]:Hide()
    end
    cache.builtZoom, cache.builtOx, cache.builtOy = zoom, ox, oy
end

local function drawArt()
    local map = maps and maps[shown]
    for _, tile in ipairs(tiles) do
        tile:Hide()
    end
    if not map then
        return
    end
    content:SetSize(map.width, map.height)
    local ok, textures = pcall(C_Map.GetMapArtLayerTextures, map.mapId, 1)
    if not ok or type(textures) ~= "table" or #textures == 0 then
        map.hasArt = false
        return
    end
    local columns = ceil(map.width / map.tileWidth)
    for index, fileId in ipairs(textures) do
        local tile = tiles[index]
        if not tile then
            tile = content:CreateTexture(nil, "BACKGROUND")
            -- (Art that is scaled must not be snapped to whole pixels, or
            -- the seams between the tiles show.)
            if tile.SetSnapToPixelGrid then
                tile:SetSnapToPixelGrid(false)
                tile:SetTexelSnappingBias(0)
            end
            tiles[index] = tile
        end
        local column, row = (index - 1) % columns, floor((index - 1) / columns)
        tile:ClearAllPoints()
        tile:SetPoint("TOPLEFT", content, "TOPLEFT", column * map.tileWidth, -row * map.tileHeight)
        tile:SetSize(map.tileWidth, map.tileHeight)
        tile:SetTexture(fileId)
        tile:Show()
    end
end

----------------------------------------------------------------------------
-- The view
----------------------------------------------------------------------------

local function applyView()
    local map = maps and maps[shown]
    if not map then
        return
    end
    zoom = max(fitZoom, min(MAX_ZOOM, zoom))
    local viewWidth, viewHeight = CANVAS_WIDTH / zoom, CANVAS_HEIGHT / zoom
    -- Keep the map in the window: centred where it is smaller than the
    -- view, never dragged past its edge where it is larger.
    if viewWidth >= map.width then
        ox = (map.width - viewWidth) / 2
    else
        ox = max(0, min(map.width - viewWidth, ox))
    end
    if viewHeight >= map.height then
        oy = (map.height - viewHeight) / 2
    else
        oy = max(0, min(map.height - viewHeight, oy))
    end
    content:SetScale(zoom)
    content:ClearAllPoints()
    content:SetPoint("TOPLEFT", canvas, "TOPLEFT", -ox, oy)
end

local function fit()
    local map = maps and maps[shown]
    if not map then
        return
    end
    fitZoom = min(CANVAS_WIDTH / map.width, CANVAS_HEIGHT / map.height)
    zoom, ox, oy = fitZoom, 0, 0
    applyView()
end

-- Where the cursor is on the canvas (from its top-left corner, in canvas
-- units), or nil.
local function cursorOnCanvas()
    local x, y = GetCursorPosition()
    local scale = canvas:GetEffectiveScale()
    local left, top = canvas:GetLeft(), canvas:GetTop()
    if not x or not left or not scale or scale <= 0 then
        return nil
    end
    return x / scale - left, top - y / scale
end

local function zoomBy(factor, cx, cy)
    cx, cy = cx or CANVAS_WIDTH / 2, cy or CANVAS_HEIGHT / 2
    local before = zoom
    local wanted = max(fitZoom, min(MAX_ZOOM, zoom * factor))
    if wanted == before then
        return
    end
    -- The point under the cursor stays under the cursor.
    ox = ox + cx / before - cx / wanted
    oy = oy + cy / before - cy / wanted
    zoom = wanted
    applyView()
    rebuildAt = GetTime() + REBUILD_DELAY
end

local function centreOn(x, y)
    ox, oy = x - CANVAS_WIDTH / zoom / 2, y - CANVAS_HEIGHT / zoom / 2
    applyView()
    rebuildAt = GetTime()
end

----------------------------------------------------------------------------
-- What the window says
----------------------------------------------------------------------------

local function refreshText()
    local map = maps and maps[shown]
    parts.where:SetText(map and ("road - " .. map.name) or "road")
    local db = RoadRecorder.GetDatabase()
    local yards = db and db.yards or 0
    local share = yards > 0 and floor((db.beltYards or 0) / yards * 100 + 0.5) or 0
    if yards < 1 then
        parts.totals:SetText("No road recorded yet. Walka keeps your character's path from now on.")
    else
        parts.totals:SetText(string.format(
            "Road recorded: %s - %d%% of it walked on the treadmill.",
            Walka:FormatDistance(yards * 0.9144), share
        ))
    end
    local message
    if not map then
        message = "The game did not say where its continents are, so there is no map to draw on.\n"
            .. "Your road is still being recorded. (/walka mapcheck prints what the game answered.)"
    elseif cache and cache.shown == 0 then
        message = "No road here yet."
    end
    parts.message:SetText(message or "")
    parts.message:SetShown(message ~= nil)
    parts.noArt:SetShown(map ~= nil and not map.hasArt)
end

-- The "you are here" mark.
local function placeMarker()
    local map = maps and maps[shown]
    local continent, x, y = RoadRecorder.WhereAmI()
    if not map or continent ~= map.continent then
        parts.marker:Hide()
        return nil
    end
    local px, py = map.toMap(x, y)
    local cx, cy = (px - ox) * zoom, (py - oy) * zoom
    if cx < 0 or cy < 0 or cx > CANVAS_WIDTH or cy > CANVAS_HEIGHT then
        parts.marker:Hide()
    else
        parts.marker:ClearAllPoints()
        parts.marker:SetPoint("CENTER", canvas, "TOPLEFT", cx, -cy)
        local facing = GetPlayerFacing and GetPlayerFacing()
        if not isSecret(facing) and type(facing) == "number" then
            parts.arrow:SetRotation(facing)
        end
        parts.marker:Show()
    end
    return px, py
end

local function showMap(index)
    if not maps or #maps == 0 then
        maps = findMaps()
    end
    if #maps == 0 then
        cache = { chapters = {}, shown = 0 }
        refreshText()
        return
    end
    shown = ((index - 1) % #maps) + 1
    drawArt()
    fit()
    readRoad()
    drawRoad()
    refreshText()
    placeMarker()
end

-- The continent to open on: where the character is, else where most of
-- the road is.
local function bestMap()
    local here = RoadRecorder.WhereAmI()
    local db = RoadRecorder.GetDatabase()
    local summary = Road.summary(db)
    local best, bestPoints = 1, -1
    for index, map in ipairs(maps) do
        if map.continent == here then
            return index
        end
        local points = summary.continents[map.continent] or 0
        if points > bestPoints then
            best, bestPoints = index, points
        end
    end
    return best
end

----------------------------------------------------------------------------
-- Construction
----------------------------------------------------------------------------

local function applyPosition()
    if frame.walkaMoving then
        return
    end
    Widgets.Place(frame, Walka:Get("roadMap.point"), DEFAULT_POINT)
end

local function savePosition()
    local point = Widgets.Placement(frame)
    if point then
        Walka:Set("roadMap.point", point)
    end
end

local elapsedSinceMarker = 0

local function onUpdate(_, elapsed)
    if dragging then
        local cx, cy = cursorOnCanvas()
        if cx then
            ox = dragging.ox - (cx - dragging.cx) / zoom
            oy = dragging.oy - (cy - dragging.cy) / zoom
            applyView()
        end
    end
    if rebuildAt and GetTime() >= rebuildAt and not dragging then
        drawRoad()
    end
    elapsedSinceMarker = elapsedSinceMarker + (elapsed or 0)
    if elapsedSinceMarker >= 0.1 then
        elapsedSinceMarker = 0
        placeMarker()
    end
end

local function legendSwatch(parent, color, text, anchor)
    local swatch = Widgets.Fill(parent, "ARTWORK", color[1], color[2], color[3], 1)
    swatch:SetSize(14, 4)
    if anchor then
        swatch:SetPoint("LEFT", anchor, "RIGHT", 10, 0)
    end
    local label = Widgets.Text(parent, "GameFontDisableSmall", text, "LEFT")
    label:SetPoint("LEFT", swatch, "RIGHT", 4, 0)
    return swatch, label
end

local function create()
    if frame then
        return
    end
    frame = Widgets.Panel(UIParent, "WalkaRoadMapFrame", 0.96)
    frame:SetSize(WIDTH, HEIGHT)
    frame:SetFrameStrata("HIGH")
    frame:SetToplevel(true)
    frame:Hide()
    Widgets.MakeMovable(frame, nil, savePosition)
    applyPosition()
    if type(UISpecialFrames) == "table" then
        table.insert(UISpecialFrames, "WalkaRoadMapFrame")
    end

    local title = Widgets.Text(frame, "GameFontNormalLarge", "Walka", "LEFT")
    title:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN + 2, -9)
    parts.where = Widgets.Text(frame, "GameFontHighlight", "road", "LEFT")
    parts.where:SetPoint("BOTTOMLEFT", title, "BOTTOMRIGHT", 6, 1)
    parts.where:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    local close = Widgets.CloseButton(frame, function()
        frame:Hide()
    end)
    close:ClearAllPoints()
    close:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -3, -3)

    -- Buttons, right to left.
    local anchor = close
    local function button(text, width, tip, onClick)
        local item = Widgets.Button(frame, text, width, onClick)
        item:SetPoint("RIGHT", anchor, "LEFT", anchor == close and -6 or -4, anchor == close and -1 or 0)
        Widgets.Tooltip(item, text, tip, "ANCHOR_BOTTOMRIGHT")
        anchor = item
        return item
    end
    if Screenshot then
        parts.screenshot = button("Screenshot", 90,
            "Saves a picture of the whole screen, this window included, in the Screenshots folder of your "
                .. "World of Warcraft installation.",
            function(self)
                Widgets.HideTooltip(self)
                Screenshot()
            end)
    end
    parts.fit = button("Fit", 44, "Show the whole map.", function()
        fit()
        rebuildAt = GetTime()
    end)
    parts.me = button("Me", 44, "Centre the map on your character.", function()
        local px, py = placeMarker()
        if px then
            centreOn(px, py)
        end
    end)
    parts.next = button(">", 26, "The next continent, or other land your road is on.", function()
        showMap(shown + 1)
    end)
    parts.previous = button("<", 26, "The previous continent, or other land your road is on.", function()
        showMap(shown - 1)
    end)

    -- The map.
    canvas = CreateFrame("Frame", nil, frame)
    canvas:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -HEADER)
    canvas:SetSize(CANVAS_WIDTH, CANVAS_HEIGHT)
    canvas:SetClipsChildren(true)
    local backdrop = Widgets.Fill(canvas, "BACKGROUND", 0.03, 0.05, 0.08, 1)
    backdrop:SetAllPoints(canvas)
    content = CreateFrame("Frame", nil, canvas)
    content:SetSize(1002, 668)
    content:SetPoint("TOPLEFT", canvas, "TOPLEFT", 0, 0)

    -- Things that keep their size whatever the zoom.
    overlay = CreateFrame("Frame", nil, canvas)
    overlay:SetAllPoints(canvas)
    overlay:SetFrameLevel(content:GetFrameLevel() + 5)
    parts.marker = CreateFrame("Frame", nil, overlay)
    parts.marker:SetSize(24, 24)
    local dot = Widgets.Fill(parts.marker, "ARTWORK", 1, 0.9, 0.3, 1)
    dot:SetSize(6, 6)
    dot:SetPoint("CENTER")
    parts.arrow = parts.marker:CreateTexture(nil, "OVERLAY")
    parts.arrow:SetAllPoints(parts.marker)
    -- (The game's own "you are here" arrow. Should the file not load in
    -- some client build, the dot under it still shows.)
    parts.arrow:SetTexture("Interface\\WorldMap\\WorldMapArrow")
    parts.marker:Hide()

    parts.message = Widgets.Text(overlay, "GameFontHighlight", "", "CENTER")
    parts.message:SetPoint("CENTER", overlay, "CENTER", 0, 0)
    parts.message:SetWidth(CANVAS_WIDTH - 80)
    parts.noArt = Widgets.Text(overlay, "GameFontDisableSmall",
        "The game gave no map picture for this land; the road is drawn without it.", "LEFT")
    parts.noArt:SetPoint("BOTTOMLEFT", overlay, "BOTTOMLEFT", 6, 6)
    parts.noArt:Hide()

    canvas:EnableMouse(true)
    canvas:EnableMouseWheel(true)
    canvas:SetScript("OnMouseWheel", function(_, delta)
        local cx, cy = cursorOnCanvas()
        zoomBy(delta > 0 and ZOOM_STEP or 1 / ZOOM_STEP, cx, cy)
    end)
    canvas:SetScript("OnMouseDown", function(_, mouseButton)
        if mouseButton == "LeftButton" then
            local cx, cy = cursorOnCanvas()
            if cx then
                dragging = { cx = cx, cy = cy, ox = ox, oy = oy }
            end
        end
    end)
    canvas:SetScript("OnMouseUp", function()
        if dragging then
            dragging = nil
            -- The road was drawn for the old view: draw what is in sight now.
            rebuildAt = GetTime()
        end
    end)

    -- Legend and totals.
    local legend = Widgets.Text(frame, "GameFontDisableSmall", "level", "LEFT")
    legend:SetPoint("TOPLEFT", canvas, "BOTTOMLEFT", 2, -8)
    local labels = { "1-9", "10-19", "20-29", "30-39", "40-49", "50-60" }
    local last = legend
    for index, color in ipairs(RoadMap.LEVEL_COLORS) do
        local _, label = legendSwatch(frame, color, labels[index], last)
        last = label
    end
    local hint = Widgets.Text(frame, "GameFontDisableSmall",
        "bright: walked on the treadmill - wheel zooms, drag moves", "RIGHT")
    hint:SetPoint("TOPRIGHT", canvas, "BOTTOMRIGHT", -2, -8)
    parts.totals = Widgets.Text(frame, "GameFontHighlightSmall", "", "LEFT")
    parts.totals:SetPoint("TOPLEFT", canvas, "BOTTOMLEFT", 2, -26)

    frame:SetScript("OnUpdate", onUpdate)
    Widgets.Hook(frame, "OnShow", function(self)
        self:Raise()
        maps = nil
        cache = nil
        maps = findMaps()
        showMap(#maps > 0 and bestMap() or 1)
    end)
end

----------------------------------------------------------------------------
-- Public API
----------------------------------------------------------------------------

function RoadMap.Show()
    if not Walka.loaded then
        return
    end
    create()
    if not frame:IsShown() then
        frame:Show()
    end
end

function RoadMap.Hide()
    if frame and frame:IsShown() then
        frame:Hide()
    end
end

function RoadMap.Toggle()
    if frame and frame:IsShown() then
        RoadMap.Hide()
    else
        RoadMap.Show()
    end
end

--- What the game answers to the questions this window asks: printed by
-- /walka mapcheck, for finding out why a map does not show.
function RoadMap.Check(print)
    local found = findMaps()
    print(string.format("map check: %d continent map(s) found.", #found))
    for _, map in ipairs(found) do
        local ok, textures = pcall(C_Map.GetMapArtLayerTextures, map.mapId, 1)
        print(string.format(
            "  %s (map %d, continent %d): art %dx%d in %dx%d tiles, %s picture file(s).",
            map.name, map.mapId, map.continent, map.width, map.height, map.tileWidth, map.tileHeight,
            ok and type(textures) == "table" and tostring(#textures) or "no"
        ))
    end
    local continent, x, y, mapId = RoadRecorder.WhereAmI()
    if continent then
        print(string.format("  you are on continent %d at %d, %d (world yards), on map %s.",
            continent, x, y, tostring(mapId)))
    else
        print("  the game does not say where you are right now (an instance, or a loading screen).")
    end
    RoadRecorder.Flush()
    local db = RoadRecorder.GetDatabase()
    local summary = Road.summary(db)
    -- Which map each land the character has stood on is drawn on (a land
    -- the game lists under no continent is found by this alone).
    local lands = {}
    for land in pairs(db and db.maps or {}) do
        lands[#lands + 1] = land
    end
    table.sort(lands)
    for _, land in ipairs(lands) do
        local kept = db.maps[land]
        print(string.format("  continent %d is drawn on map %d (%s); %d point(s) of road there.",
            land, kept.id, kept.name or "no name", summary.continents[land] or 0))
    end
    print(string.format("  road: %d stretches, %d points, %s; recording is %s.",
        summary.segments, summary.points, Walka:FormatDistance(summary.yards * 0.9144),
        RoadRecorder.IsEnabled() and "on" or "off"))
    if RoadRecorder.problem then
        print("  the recorder ran into: " .. RoadRecorder.problem)
    end
end

Walka:On("SETTINGS", function()
    if frame then
        applyPosition()
        refreshText()
    end
end)

-- The road has reached a land whose map was not known: it joins the maps
-- the window goes through. What is on show stays as it is.
Walka:On("ROAD_MAPS", function()
    if not (frame and frame:IsShown() and maps) then
        return
    end
    local showing = maps[shown] and maps[shown].mapId
    maps = findMaps()
    for index, map in ipairs(maps) do
        if map.mapId == showing then
            shown = index
            return
        end
    end
    showMap(#maps > 0 and bestMap() or 1)
end)

return RoadMap
