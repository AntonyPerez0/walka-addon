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

local floor, sqrt, abs = math.floor, math.sqrt, math.abs
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
