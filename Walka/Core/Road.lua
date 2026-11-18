-- Walka road: the path a character has travelled, kept compactly.
--
-- Pure Lua, no game calls (Road glue is in RoadRecorder.lua): everything
-- here runs under the offline tests.
--
-- What is kept (in the per-character saved variable WalkaCharDB):
--
--   { version = 1, guid = "...", key = "Name-Realm",
--     yards = n,         -- length of the road recorded so far
--     beltYards = n,     -- ... of which with the treadmill belt moving
--     segments = { segment, ... } }
--
-- A segment is one unbroken stretch travelled the same way:
--
--   { c = continent id,
--     m = "w" on foot | "r" mounted | "t" flight path | "g" as a ghost,
--     b = 1 if the treadmill belt was moving (absent otherwise),
--     l = character level,
--     t = time of its first point (seconds since the epoch),
--     x = .., y = ..     its first point, in whole world yards,
--     n = number of points after the first,
--     d = "dt,dx,dy;dt,dx,dy;..." }
--
-- `d` holds the further points as whole-number steps from the point before
-- (seconds, yards, yards). Text like this loads far faster than a table per
-- point and stays small: a road from level 1 to 60 is on the order of a
-- hundred thousand points and about a megabyte.
--
-- How points are chosen follows Road to Sixty (github.com/Jordanburch101/
-- road-to-sixty, MIT licence, (c) 2026 Jordan Burch), whose recorder has run
-- in the real client: a point every 20 yards (75 on a flight path), sooner
-- where the road bends, nothing while standing still, and a new segment
-- wherever the character jumps (a teleport, a loading screen, a new
-- continent). The code is Walka's own. See THIRD-PARTY-NOTICES.md.

local ADDON_NAME, ns = ...

local Road = {}
ns.Road = Road

local floor, sqrt, abs, max, min = math.floor, math.sqrt, math.abs, math.max, math.min
local concat = table.concat

Road.VERSION = 1

Road.STORE_YARDS = 20           -- a point at least this often ...
Road.STORE_YARDS_FLIGHT = 75    -- ... on a flight path
Road.BEND_YARDS = 2             -- sooner if the straight line would cut a corner by more than this
Road.MIN_YARDS = 3              -- but never closer together than this
Road.JUMP_YARDS = 300           -- further than this between two looks is not travel
Road.MAX_HELD = 120             -- samples remembered since the last stored point
Road.MAX_SEGMENT_POINTS = 2000  -- a longer stretch is carried on in a new segment

Road.MODES = { w = true, r = true, t = true, g = true }

local function isNumber(value)
    return type(value) == "number" and value == value and value > -math.huge and value < math.huge
end

--- Make `saved` (what the game loaded for this character, possibly nothing
-- or damaged) into a road database for the character `guid` / `key`.
-- The game keeps per-character files by name, so a new character with the
-- name of a deleted one inherits its file: a GUID that differs starts the
-- road afresh.
function Road.database(saved, guid, key)
    local db = type(saved) == "table" and saved or {}
    local sameCharacter = guid == nil or db.guid == nil or db.guid == guid
    if db.version ~= Road.VERSION or type(db.segments) ~= "table" or not sameCharacter then
        db.segments = {}
        db.yards, db.beltYards = 0, 0
    end
    db.version = Road.VERSION
    db.guid = guid or db.guid
    db.key = key or db.key
    db.yards = isNumber(db.yards) and db.yards or 0
    db.beltYards = isNumber(db.beltYards) and db.beltYards or 0
    -- A damaged file can hold anything: keep what looks like a segment.
    local kept = {}
    for _, segment in ipairs(db.segments) do
        if type(segment) == "table" and isNumber(segment.c) and isNumber(segment.x) and isNumber(segment.y)
            and isNumber(segment.t) and Road.MODES[segment.m] and (segment.d == nil or type(segment.d) == "string") then
            kept[#kept + 1] = segment
        end
    end
    db.segments = kept
    return db
end

----------------------------------------------------------------------------
-- Recording
----------------------------------------------------------------------------

local Recorder = {}
Recorder.__index = Recorder
Road.Recorder = Recorder

--- A recorder that appends to `db` (see Road.database).
function Recorder.new(db)
    local self = setmetatable({}, Recorder)
    self.db = db
    self.segment = nil      -- the open segment (already in db.segments)
    self.parts = {}         -- its steps as text, joined into segment.d on flush
    self.dirty = false      -- steps not yet joined into segment.d
    self.lastX, self.lastY, self.lastT = 0, 0, 0     -- the last stored point
    self.prevX, self.prevY = nil, nil                -- the last look
    self.held = {}          -- looks since the last stored point: x1, y1, x2, y2, ...
    self.heldCount = 0
    self.beltLooks, self.looks = 0, 0                -- since the last stored point
    return self
end

local function distance(ax, ay, bx, by)
    local dx, dy = bx - ax, by - ay
    return sqrt(dx * dx + dy * dy)
end

-- How far the point (px, py) is from the straight line a -> b.
local function offLine(ax, ay, bx, by, px, py)
    local dx, dy = bx - ax, by - ay
    local length = sqrt(dx * dx + dy * dy)
    if length <= 0 then
        return distance(ax, ay, px, py)
    end
    return abs(dx * (py - ay) - dy * (px - ax)) / length
end

function Recorder:clearHeld()
    self.heldCount = 0
    self.beltLooks, self.looks = 0, 0
end

--- Write the open segment's steps into the saved table. Cheap enough to
-- call once a minute; the game only saves what is in the table when it
-- logs out or reloads.
function Recorder:flush()
    local segment = self.segment
    if segment and self.dirty then
        segment.d = concat(self.parts, ";")
        segment.n = #self.parts
        self.dirty = false
    end
end

-- Start a segment at (x, y).
function Recorder:open(now, continent, x, y, mode, belt, level)
    local segment = { c = continent, m = mode, l = level, t = now, x = x, y = y, n = 0 }
    if belt then
        segment.b = 1
    end
    local segments = self.db.segments
    segments[#segments + 1] = segment
    self.segment = segment
    self.parts = {}
    self.dirty = false
    self.lastX, self.lastY, self.lastT = x, y, now
    self:clearHeld()
end

-- Add (x, y) to the open segment.
function Recorder:store(now, x, y)
    local segment = self.segment
    local dx, dy = x - self.lastX, y - self.lastY
    if dx == 0 and dy == 0 then
        return
    end
    local parts = self.parts
    parts[#parts + 1] = floor(now - self.lastT + 0.5) .. "," .. dx .. "," .. dy
    self.dirty = true
    local yards = sqrt(dx * dx + dy * dy)
    self.db.yards = self.db.yards + yards
    if segment.b then
        self.db.beltYards = self.db.beltYards + yards
    end
    self.lastX, self.lastY, self.lastT = x, y, now
    self:clearHeld()
end

--- End the open segment (the character jumped, or cannot be located). The
-- last place it was seen at is kept, so the road does not stop short.
function Recorder:close(now)
    local segment = self.segment
    if not segment then
        return
    end
    if self.prevX and now and distance(self.lastX, self.lastY, self.prevX, self.prevY) >= 1 then
        self:store(now, self.prevX, self.prevY)
    end
    self:flush()
    if (segment.n or 0) == 0 then
        -- A single point is no road: drop it.
        local segments = self.db.segments
        if segments[#segments] == segment then
            segments[#segments] = nil
        end
    end
    self.segment = nil
    self.prevX, self.prevY = nil, nil
    self:clearHeld()
end

-- Carry on in a new segment that begins where the open one ends (the way
-- of travelling changed, not the place): the line stays in one piece.
function Recorder:carryOn(now, x, y, mode, belt, level)
    local continent = self.segment.c
    if distance(self.lastX, self.lastY, x, y) >= 1 then
        self:store(now, x, y)
    end
    self:flush()
    local previous = self.segment
    if (previous.n or 0) == 0 then
        local segments = self.db.segments
        if segments[#segments] == previous then
            segments[#segments] = nil
        end
    end
    self:open(now, continent, self.lastX, self.lastY, mode, belt, level)
end

--- One look at the character.
--   now        seconds since the epoch
--   continent  the continent id, or nil if the character cannot be located
--              (inside a dungeon, during a loading screen)
--   x, y       its place, in whole world yards
--   mode       "w" | "r" | "t" | "g"
--   belt       true if the treadmill belt is moving
--   level      the character's level
function Recorder:sample(now, continent, x, y, mode, belt, level)
    if continent == nil or not isNumber(x) or not isNumber(y) or not isNumber(now) then
        self:close(isNumber(now) and now or nil)
        return
    end
    if not Road.MODES[mode] then
        mode = "w"
    end
    belt = belt and true or false
    local segment = self.segment
    if segment and (segment.c ~= continent or (self.prevX and distance(self.prevX, self.prevY, x, y) > Road.JUMP_YARDS)) then
        -- Another continent, or too far to have been travelled: a jump.
        self:close(now)
        segment = nil
    end
    if not segment then
        self:open(now, continent, x, y, mode, belt, level)
        self.prevX, self.prevY = x, y
        return
    end

    if segment.m ~= mode or segment.l ~= level then
        self:carryOn(now, x, y, mode, belt, level)
        self.prevX, self.prevY = x, y
        return
    end

    -- Is the belt moving on this stretch? Decided when a point is stored,
    -- by what most of the looks since the last one said.
    self.looks = self.looks + 1
    if belt then
        self.beltLooks = self.beltLooks + 1
    end

    local moved = distance(self.lastX, self.lastY, x, y)
    local every = mode == "t" and Road.STORE_YARDS_FLIGHT or Road.STORE_YARDS
    local storeX, storeY
    if moved >= every then
        storeX, storeY = x, y
    elseif moved >= Road.MIN_YARDS and self.heldCount > 0 then
        -- A bend: would the straight line from the last stored point to
        -- here cut past one of the places in between by too much? Then the
        -- place seen just before is stored, where the road still fits.
        local held = self.held
        for index = 1, self.heldCount * 2, 2 do
            if offLine(self.lastX, self.lastY, x, y, held[index], held[index + 1]) > Road.BEND_YARDS then
                local px, py = self.prevX, self.prevY
                if px and distance(self.lastX, self.lastY, px, py) >= Road.MIN_YARDS then
                    storeX, storeY = px, py
                end
                break
            end
        end
    end

    if storeX then
        local onBelt = self.beltLooks * 2 > self.looks
        if (segment.b == 1) ~= onBelt then
            -- The belt started or stopped on this stretch: it begins a new
            -- segment, from the last stored point.
            self:flush()
            self:open(self.lastT, continent, self.lastX, self.lastY, mode, onBelt, level)
            local segments = self.db.segments
            local previous = segments[#segments - 1]
            if previous and (previous.n or 0) == 0 then
                table.remove(segments, #segments - 1)
            end
        end
        self:store(now, storeX, storeY)
        if #self.parts >= Road.MAX_SEGMENT_POINTS then
            self:flush()
            self:open(now, continent, self.lastX, self.lastY, mode, onBelt, level)
        end
        if storeX ~= x or storeY ~= y then
            -- (the look that showed the bend is the first one held after it)
            self.held[1], self.held[2] = x, y
            self.heldCount = 1
            self.looks, self.beltLooks = 1, belt and 1 or 0
        end
    elseif moved > 0 and (x ~= self.prevX or y ~= self.prevY) then
        local count = self.heldCount
        if count < Road.MAX_HELD then
            self.held[count * 2 + 1], self.held[count * 2 + 2] = x, y
            self.heldCount = count + 1
        end
    end
    self.prevX, self.prevY = x, y
end

----------------------------------------------------------------------------
-- Reading it back
----------------------------------------------------------------------------

--- The points of a segment as two arrays of world yards (filled into `xs`
-- and `ys`, which are returned with the number of points). A segment whose
-- text is damaged yields the points up to the damage.
function Road.points(segment, xs, ys)
    xs, ys = xs or {}, ys or {}
    local x, y = segment.x, segment.y
    local count = 1
    xs[1], ys[1] = x, y
    local steps = segment.d
    if type(steps) == "string" then
        for dx, dy in steps:gmatch("%-?%d+,(%-?%d+),(%-?%d+)") do
            x, y = x + tonumber(dx), y + tonumber(dy)
            count = count + 1
            xs[count], ys[count] = x, y
        end
    end
    for index = count + 1, #xs do
        xs[index], ys[index] = nil, nil
    end
    return xs, ys, count
end

--- Which of `count` points are needed to draw the line to within
-- `tolerance` (in the same units as the points)? Fills `keep` with true at
-- those indices and returns it and how many there are. The first and last
-- point are always kept. (Ramer-Douglas-Peucker, without recursion.)
function Road.simplify(xs, ys, count, tolerance, keep)
    keep = keep or {}
    for index = 1, count do
        keep[index] = false
    end
    if count <= 2 or tolerance <= 0 then
        for index = 1, count do
            keep[index] = true
        end
        return keep, count
    end
    keep[1], keep[count] = true, true
    local kept = 2
    local stack = { 1, count }
    local top = 2
    while top > 0 do
        local last, first = stack[top], stack[top - 1]
        top = top - 2
        local worst, worstIndex = tolerance, nil
        local ax, ay, bx, by = xs[first], ys[first], xs[last], ys[last]
        for index = first + 1, last - 1 do
            local off = offLine(ax, ay, bx, by, xs[index], ys[index])
            if off > worst then
                worst, worstIndex = off, index
            end
        end
        if worstIndex then
            keep[worstIndex] = true
            kept = kept + 1
            stack[top + 1], stack[top + 2] = first, worstIndex
            stack[top + 3], stack[top + 4] = worstIndex, last
            top = top + 4
        end
    end
    return keep, kept
end

----------------------------------------------------------------------------
-- Drawing
----------------------------------------------------------------------------
-- A map draws the road as straight pieces of line. What follows turns the
-- saved road into as few pieces as the eye needs for the map as it is
-- shown, at a cost that goes with what is drawn, not with how long the
-- road has become (a road from level 1 to 60 is over a hundred thousand
-- points in thousands of segments):
--
--   chapters  The segments are read into chapters: runs of road that carry
--             on from one another, some hundreds of points each, whatever
--             the level or the way of travelling. A map looks at a few
--             hundred chapters, not at thousands of segments.
--   rungs     Each chapter keeps a ladder of coarser and coarser copies of
--             itself, made once. A map of the whole world draws from the
--             coarsest; nothing ever thins a hundred thousand points while
--             the map is being moved.
--   lines     For a view, the chapters in sight are drawn from the finest
--             rung that does not come to hopelessly many points, bends too
--             small to see are cut straight, and a run of pieces each too
--             short to see is drawn as one.

Road.CHAPTER_POINTS = 200   -- a chapter is closed once it has about this many points
Road.LADDER_YARDS = 12      -- the first coarser copy of a chapter is true to this many yards ...
Road.LADDER_STEP = 4        -- ... and each further one to this many times the one before
Road.LADDER_RUNGS = 5       -- 12, 48, 192, 768 and 3072 yards
Road.LINES_START = 1.2      -- start from the finest rung with at most this many points per piece wanted
Road.LINES_GROW = 1.3       -- too many pieces: draw at least this much more coarsely and count again
Road.LINES_PASSES = 16      -- ... but not for ever

local huge = math.huge
local gmatch = string.gmatch

-- What has been read of a road database, by its table (forgotten with it).
local reads = setmetatable({}, { __mode = "k" })
local ladderKeep, linesKeep = {}, {}

local function newChapter(continent, first, joined)
    return {
        c = continent, xs = {}, ys = {}, own = {}, count = 0,
        minX = huge, maxX = -huge, minY = huge, maxY = -huge,
        looks = {}, joined = joined, first = first, rungs = {}, ready = false,
    }
end

-- Add a segment's points to a chapter. A segment that carries on from the
-- chapter's last point does not repeat it.
local function addSegment(chapter, segment)
    local xs, ys, own = chapter.xs, chapter.ys, chapter.own
    local looks = chapter.looks
    local look = #looks + 1
    looks[look] = { mode = segment.m, belt = segment.b == 1, level = segment.l }
    local count = chapter.count
    local x, y = segment.x, segment.y
    local minX, maxX, minY, maxY = chapter.minX, chapter.maxX, chapter.minY, chapter.maxY
    if count == 0 then
        count = 1
        xs[1], ys[1], own[1] = x, y, look
        minX, maxX, minY, maxY = x, x, y, y
    end
    for dx, dy in gmatch(segment.d, "%-?%d+,(%-?%d+),(%-?%d+)") do
        x, y = x + tonumber(dx), y + tonumber(dy)
        count = count + 1
        xs[count], ys[count], own[count] = x, y, look
        if x < minX then minX = x end
        if x > maxX then maxX = x end
        if y < minY then minY = y end
        if y > maxY then maxY = y end
    end
    chapter.count = count
    chapter.minX, chapter.maxX, chapter.minY, chapter.maxY = minX, maxX, minY, maxY
end

--- The road of a database as chapters, for drawing:
--   { c = continent, xs = .., ys = .., count = number of points,
--     own = for each point, which of `looks` the piece of road ending at
--           it has,
--     looks = { { mode = "w"|"r"|"t"|"g", belt = true if the treadmill belt
--               was moving, level = .. }, ... } (one for each segment in it),
--     minX, maxX, minY, maxY (the box it lies in, world yards),
--     joined = true if it begins exactly where the chapter before it ends }
-- Asked again as the road grows, only its end is read anew. `expired`, if
-- given, is called after every segment: when it returns true the reading
-- stops there and is taken up at the next call (for reading a long road a
-- little at a time). Returns the chapters and whether the whole road has
-- been read.
function Road.chapters(db, expired)
    local segments = type(db) == "table" and db.segments
    if type(segments) ~= "table" then
        return {}, true
    end
    local read = reads[db]
    if not read or read.segments ~= segments then
        read = { segments = segments, plan = {}, chapters = {}, at = 0 }
        reads[db] = read
    end
    local plan, chapters = read.plan, read.chapters

    -- What was read before: does it still stand? Only the end of a road
    -- changes (the segment being recorded grows, an empty one is dropped).
    for index = 1, read.at do
        local entry, segment = plan[index], segments[index]
        if segment == nil or entry.segment ~= segment or entry.n ~= (segment.n or 0) or entry.d ~= segment.d then
            -- From the chapter this segment is in, everything is read again.
            local from = #chapters + 1
            for later = index, read.at do
                if plan[later].chapter then
                    from = plan[later].chapter
                    break
                end
            end
            local at = chapters[from] and chapters[from].first - 1 or index - 1
            for stale = #chapters, from, -1 do
                chapters[stale] = nil
            end
            for stale = read.at, at + 1, -1 do
                plan[stale] = nil
            end
            read.at = at
            break
        end
    end

    local chapter = chapters[#chapters]
    for index = read.at + 1, #segments do
        local segment = segments[index]
        local entry = { segment = segment, n = segment.n or 0, d = segment.d }
        plan[index] = entry
        if entry.n >= 1 and type(segment.d) == "string" then
            local carriesOn = chapter ~= nil and chapter.c == segment.c
                and chapter.xs[chapter.count] == segment.x and chapter.ys[chapter.count] == segment.y
            if not carriesOn or chapter.count + entry.n > Road.CHAPTER_POINTS then
                chapter = newChapter(segment.c, index, carriesOn)
                chapters[#chapters + 1] = chapter
            end
            addSegment(chapter, segment)
            chapter.rungs, chapter.ready = {}, false
            entry.chapter = #chapters
            if chapter.count < 2 then
                -- (Its text was damaged from the start: a point is no road.)
                chapters[#chapters] = nil
                entry.chapter = nil
                chapter = chapters[#chapters]
            end
        end
        read.at = index
        if expired and index < #segments and expired() then
            return chapters, false
        end
    end
    return chapters, true
end

-- The ladder: rung 1 is the chapter thinned to LADDER_YARDS, each further
-- rung the one before thinned to LADDER_STEP times that. Made from the
-- rung below the first time it is asked for. Returns the rung (a table
-- with xs, ys, own and count) - or the chapter itself for rung 0.
local function rungOf(chapter, rung)
    local from = chapter
    local step = Road.LADDER_YARDS
    for index = 1, rung do
        local copy = chapter.rungs[index]
        if not copy then
            local xs, ys, own, count = from.xs, from.ys, from.own, from.count
            if count <= 2 then
                copy = from     -- nothing left to thin
            else
                local keep = Road.simplify(xs, ys, count, step, ladderKeep)
                local cx, cy, co, kept = { xs[1] }, { ys[1] }, { own[1] }, 1
                -- A piece of the copy stands for several of the finer one:
                -- it takes the look of the longest of them.
                local longest, look = -1, own[1]
                for point = 2, count do
                    local dx, dy = xs[point] - xs[point - 1], ys[point] - ys[point - 1]
                    local length = dx * dx + dy * dy
                    if length > longest then
                        longest, look = length, own[point]
                    end
                    if keep[point] then
                        kept = kept + 1
                        cx[kept], cy[kept], co[kept] = xs[point], ys[point], look
                        longest = -1
                    end
                end
                copy = { xs = cx, ys = cy, own = co, count = kept }
            end
            chapter.rungs[index] = copy
        end
        from = copy
        step = step * Road.LADDER_STEP
    end
    return from
end

--- The points of a chapter for a drawing that need be no truer than
-- `tolerance` yards: the chapter itself, or the coarsest of its ladder of
-- thinned copies that is still true enough (a copy true to T yards is used
-- from a tolerance of 2 T on) - but none finer than rung `least`, if
-- given. Returns xs, ys, own and the number of points.
function Road.thinned(chapter, tolerance, least)
    local rung, step = 0, Road.LADDER_YARDS
    while rung < Road.LADDER_RUNGS and step * 2 <= tolerance do
        rung = rung + 1
        step = step * Road.LADDER_STEP
    end
    if least and least > rung then
        rung = least
    end
    local copy = rungOf(chapter, rung)
    return copy.xs, copy.ys, copy.own, copy.count
end

--- Make every coarser copy of a chapter now, so that drawing it later is
-- quick at any zoom.
function Road.prepare(chapter)
    if not chapter.ready then
        rungOf(chapter, Road.LADDER_RUNGS)
        chapter.ready = true
    end
end

local sight, sightPlace, sightScale = {}, {}, {}
local rungSums = {}

--- The pieces of line that draw `chapters` on a map.
--   placeOf(chapter)  how world yards become map units for that chapter:
--                     { ux, uy, u0, vx, vy, v0 }, meaning
--                     x = u0 + ux * worldX + uy * worldY (to the right) and
--                     y = v0 + vx * worldX + vy * worldY (down) - or nil
--                     if the map does not show where the chapter lies
--   view              { left, top, right, bottom } (what is to be drawn, in
--                     map units), tolerance (how far off a line may be: a
--                     little under a pixel) and most (pieces at most)
--   out               filled and returned: out.x1[i], out.y1[i], out.x2[i],
--                     out.y2[i] and out.from[i] (the look of the piece: a
--                     table with mode, belt and level - that of the longest
--                     stretch of road the piece stands for), with out.count
--                     pieces, out.passes, out.tolerance (as drawn) and
--                     out.cut (pieces left out because even the coarsest
--                     drawing had more than `most`; normally 0)
function Road.lines(chapters, placeOf, view, out)
    out = out or {}
    out.x1, out.y1, out.x2, out.y2 = out.x1 or {}, out.y1 or {}, out.x2 or {}, out.y2 or {}
    out.from = out.from or {}
    local X1, Y1, X2, Y2, FROM = out.x1, out.y1, out.x2, out.y2, out.from
    local left, top, right, bottom = view.left, view.top, view.right, view.bottom
    local most = view.most or 1000
    local tolerance = view.tolerance
    local keep = linesKeep
    local rungs = Road.LADDER_RUNGS

    -- The chapters in sight, and how many points each rung has of them.
    local seen = 0
    for rung = 0, rungs do
        rungSums[rung] = 0
    end
    for index = 1, #chapters do
        local chapter = chapters[index]
        local place = placeOf(chapter)
        if place then
            local ux, uy, u0, vx, vy, v0 = place[1], place[2], place[3], place[4], place[5], place[6]
            -- The box the chapter lies in, on the map (its four corners).
            local minX, maxX, minY, maxY = chapter.minX, chapter.maxX, chapter.minY, chapter.maxY
            local x1, y1 = u0 + ux * minX + uy * minY, v0 + vx * minX + vy * minY
            local x2, y2 = u0 + ux * maxX + uy * minY, v0 + vx * maxX + vy * minY
            local x3, y3 = u0 + ux * minX + uy * maxY, v0 + vx * minX + vy * maxY
            local x4, y4 = u0 + ux * maxX + uy * maxY, v0 + vx * maxX + vy * maxY
            local perYard = sqrt(abs(ux * vy - uy * vx))
            if perYard > 0
                and max(x1, x2, x3, x4) >= left and min(x1, x2, x3, x4) <= right
                and max(y1, y2, y3, y4) >= top and min(y1, y2, y3, y4) <= bottom then
                seen = seen + 1
                sight[seen], sightPlace[seen], sightScale[seen] = chapter, place, perYard
                Road.prepare(chapter)
                rungSums[0] = rungSums[0] + chapter.count
                for rung = 1, rungs do
                    rungSums[rung] = rungSums[rung] + chapter.rungs[rung].count
                end
            end
        end
    end
    for index = seen + 1, #sight do
        sight[index], sightPlace[index], sightScale[index] = nil, nil, nil
    end

    -- Where to start: the finest rung that is not hopelessly many points
    -- for the pieces wanted (the chapters themselves where that will do),
    -- drawn as it is: no finer than it is true.
    local least, floorYards = 0, 0
    if seen > 0 then
        while least < rungs and rungSums[least] > most * Road.LINES_START do
            least = least + 1
        end
        if least > 0 then
            floorYards = Road.LADDER_YARDS * Road.LADDER_STEP ^ (least - 1)
            tolerance = max(tolerance, floorYards * sightScale[1])
        end
    end

    local passes, count, drawnAt = 0, 0, tolerance
    local unseen = view.tolerance * view.tolerance      -- shorter than this cannot be seen at all

    -- The piece being built. It grows while it is short and the road
    -- carries on from its end: `short` is the pass's tolerance (squared),
    -- `path` how much road the piece stands for so far. A piece is not
    -- grown past a few tolerances of road, so that road wound tight on one
    -- spot is still drawn there (as a few short lines), not thinned away.
    local pending, short, reach = false, 0, 0
    local ax, ay, bx, by, from, longest, path = 0, 0, 0, 0, nil, 0, 0
    local function flush()
        pending = false
        local dx, dy = bx - ax, by - ay
        local chord = dx * dx + dy * dy
        if chord < unseen and path < reach then
            -- A speck: too short to see, and the road does not carry on
            -- from it. If it came here from the piece before, that one is
            -- drawn on to this end (the road ends where it ends); a speck
            -- by itself is left out.
            if count >= 1 and count <= most and X2[count] == ax and Y2[count] == ay then
                X2[count], Y2[count] = bx, by
            end
            return
        end
        if chord == 0 then
            return      -- (there and back again: a line needs two ends)
        end
        count = count + 1
        if count <= most then
            X1[count], Y1[count], X2[count], Y2[count], FROM[count] = ax, ay, bx, by, from
        end
    end

    local before, stalled = huge, 0
    repeat
        passes = passes + 1
        count, pending = 0, false
        -- Pieces shorter than the tolerance are joined - but a rung drawn
        -- as it is (the first pass, from a coarse copy) keeps its own
        -- corners: only what is short even for the rung below is joined.
        reach = tolerance
        if passes == 1 and least > 0 then
            reach = max(view.tolerance, tolerance / Road.LADDER_STEP)
        end
        short = reach * reach
        drawnAt = tolerance
        for index = 1, seen do
            local chapter, place = sight[index], sightPlace[index]
            local ux, uy, u0, vx, vy, v0 = place[1], place[2], place[3], place[4], place[5], place[6]
            local yards = tolerance / sightScale[index]
            local xs, ys, own, points = Road.thinned(chapter, yards, least)
            if yards > floorYards then
                Road.simplify(xs, ys, points, yards, keep)
            else
                -- (A rung drawn as it is: it was thinned to this already.)
                for point = 1, points do
                    keep[point] = true
                end
            end
            local looks = chapter.looks
            local lastX, lastY = u0 + ux * xs[1] + uy * ys[1], v0 + vx * xs[1] + vy * ys[1]
            local runX, runY = lastX, lastY     -- where the road took its present look
            local part, look = -1, own[2]       -- the longest part of the piece so far, and its look
            for point = 2, points do
                local worldX, worldY = xs[point], ys[point]
                local owner = own[point]
                local dx, dy = worldX - xs[point - 1], worldY - ys[point - 1]
                local partLength = dx * dx + dy * dy
                if partLength > part then
                    part, look = partLength, owner
                end
                local cut = keep[point]
                local x, y
                if own[point + 1] ~= owner then
                    -- The road changes its look here (the level, the belt):
                    -- a straight road is cut here too, if what ends here is
                    -- long enough to be seen.
                    x, y = u0 + ux * worldX + uy * worldY, v0 + vx * worldX + vy * worldY
                    cut = cut or (x - runX) * (x - runX) + (y - runY) * (y - runY) >= short
                    runX, runY = x, y
                end
                if cut then
                    if not x then
                        x, y = u0 + ux * worldX + uy * worldY, v0 + vx * worldX + vy * worldY
                    end
                    if (lastX < left and x < left) or (lastX > right and x > right)
                        or (lastY < top and y < top) or (lastY > bottom and y > bottom) then
                        -- Out of sight: nothing to draw.
                        if pending then
                            flush()
                        end
                    else
                        local length = (x - lastX) * (x - lastX) + (y - lastY) * (y - lastY)
                        if pending and bx == lastX and by == lastY and path < 4 * reach
                            and (bx - ax) * (bx - ax) + (by - ay) * (by - ay) < short then
                            bx, by = x, y
                            path = path + sqrt(length)
                            if length > longest then
                                longest, from = length, looks[look]
                            end
                        else
                            if pending then
                                flush()
                            end
                            pending = true
                            ax, ay, bx, by, from, longest, path = lastX, lastY, x, y, looks[look], length, sqrt(length)
                        end
                    end
                    lastX, lastY = x, y
                    part = -1
                end
            end
        end
        if pending then
            flush()
        end
        -- Too many: more coarsely, by as much as the count says is needed.
        -- (Pieces of road that do not join cannot be made fewer than they
        -- are: when drawing more coarsely stops helping, that is that.)
        if count > before * 0.9 then
            stalled = stalled + 1
        end
        before = count
        tolerance = tolerance * max(Road.LINES_GROW, (count / most) ^ 1.5)
    until count <= most or passes >= Road.LINES_PASSES or stalled >= 2

    out.count = min(count, most)
    out.cut = max(0, count - most)
    out.passes, out.tolerance = passes, drawnAt
    for index = out.count + 1, #FROM do
        FROM[index] = nil
    end
    return out
end

--- Totals of a road database, for display: yards, beltYards, segments,
-- points, and per continent id the number of points.
function Road.summary(db, out)
    out = out or {}
    out.yards = db and db.yards or 0
    out.beltYards = db and db.beltYards or 0
    out.segments, out.points = 0, 0
    out.continents = out.continents or {}
    for continent in pairs(out.continents) do
        out.continents[continent] = nil
    end
    for _, segment in ipairs(db and db.segments or {}) do
        local points = (segment.n or 0) + 1
        out.segments = out.segments + 1
        out.points = out.points + points
        out.continents[segment.c] = (out.continents[segment.c] or 0) + points
    end
    return out
end

return Road
