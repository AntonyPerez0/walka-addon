-- Walka journey: what one character has done since Walka began watching it.
--
-- Pure Lua 5.1 (no WoW API). The game side is in Tracker.lua, and the
-- numbers reach the companion app as stat frames (docs/PROTOCOL.md,
-- section 4), which shows them on its Share page.
--
-- One record per character, kept in WalkaDB.journeys under "Name-Realm":
--
--   since        when tracking began (seconds since the epoch)
--   footYd       yards travelled on foot
--   travelYd     yards travelled by any means: mounts, flight paths, flying
--                and swimming included
--   level, maxLevel, classId, faction
--                as last read from the game (0 = the game has not said)
--   played       the game's /played total in seconds, as last known
--                (0 = not known yet)
--   playedHeard  true if the game gave that figure during the login the
--                record was saved in
--   real         the real-world walking that is settled (see below):
--                { walks, seconds, distanceM, steps, kcal, estimated }
--
-- Two conventions, shown to the player as "about":
--   * a character takes 2,000 steps to the in-game mile of 1,760 yards -
--     0.88 yards a step. The game does not count steps;
--   * "on foot" is moving while not mounted, not on a flight path, not
--     flying and not swimming. Running as a ghost and jumping count.

local ADDON_NAME, ns = ...
ns = ns or {}

local Journey = {}
ns.Journey = Journey

local floor, huge = math.floor, math.huge

Journey.STEPS_PER_MILE = 2000
Journey.YARDS_PER_MILE = 1760
Journey.METERS_PER_YARD = 0.9144
-- The level cap of WoW: Forever, used until the client names another.
Journey.DEFAULT_MAX_LEVEL = 60

-- Yards per second. Nothing in the game moves a character this fast, so a
-- larger "speed" is not movement - and one stray infinite value would
-- otherwise end up in the saved variables, which the game then cannot read.
local MAX_SPEED = 1000

-- Times a journey can have begun at: not before 2020 - there was no Walka
-- to track anything, so an earlier time comes from a damaged file (the
-- companion draws the same line) - and not after what the game's date()
-- can format (see SessionLog.MAX_EPOCH).
local MIN_EPOCH = 1577836800
local MAX_EPOCH = 32503680000

-- A finite number that is not negative, or 0. The records live in the saved
-- variables - a file on disk - and are added to for as long as the
-- character exists, so whatever is in the file must not spread.
local function count(value)
    value = tonumber(value)
    if value and value > 0 and value ~= huge then    -- (NaN fails `> 0`)
        return value
    end
    return 0
end

-- A whole number from 0 to 255: one byte of the CHAR_INFO stat.
local function byte(value)
    value = floor(count(value))
    if value > 255 then
        return 255
    end
    return value
end

-- A time a journey can have begun at, or nil.
local function usableTime(value)
    value = tonumber(value)
    if value and value >= MIN_EPOCH and value < MAX_EPOCH then
        return floor(value)
    end
    return nil
end

--- Is `key` the name of a character? The addon files a session under "?"
-- when the game hid the character's name as it began: such a walk belongs
-- to nobody's journey.
function Journey.isCharacter(key)
    return type(key) == "string" and key ~= "" and string.sub(key, 1, 1) ~= "?"
end

----------------------------------------------------------------------------
-- Records
----------------------------------------------------------------------------

--- The table of journeys in the saved variables, created if this is the
-- first time (a database from before journeys existed needs nothing else:
-- its walks are read straight from the session history).
function Journey.database(db)
    if type(db.journeys) ~= "table" then
        db.journeys = {}
    end
    return db.journeys
end

--- The journey record of `key`, created if the character is new, with
-- every field made safe to count with. `now` is when tracking begins for a
-- new character.
function Journey.open(journeys, key, now)
    local record = journeys[key]
    if type(record) ~= "table" then
        record = {}
        journeys[key] = record
    end
    record.since = usableTime(record.since) or usableTime(now) or MIN_EPOCH
    record.footYd = count(record.footYd)
    record.travelYd = count(record.travelYd)
    record.level = byte(record.level)
    record.maxLevel = byte(record.maxLevel)
    record.classId = byte(record.classId)
    record.faction = byte(record.faction)
    record.played = count(record.played)
    record.playedHeard = record.playedHeard == true or nil

    local real = type(record.real) == "table" and record.real or {}
    record.real = real
    real.walks = floor(count(real.walks))
    real.seconds = count(real.seconds)
    real.distanceM = count(real.distanceM)
    real.steps = count(real.steps)
    real.kcal = count(real.kcal)
    real.estimated = real.estimated == true
    return record
end

--- Remember the character's level, level cap, class and faction. A 0 means
-- "the game did not say" (or hid it): what is already known is kept.
function Journey.setInfo(record, level, maxLevel, classId, faction)
    level, maxLevel, classId, faction = byte(level), byte(maxLevel), byte(classId), byte(faction)
    if level > 0 then
        record.level = level
    end
    if maxLevel > 0 then
        record.maxLevel = maxLevel
    elseif record.maxLevel == 0 then
        record.maxLevel = Journey.DEFAULT_MAX_LEVEL
    end
    if classId > 0 then
        record.classId = classId
    end
    if faction > 0 then
        record.faction = faction
    end
    -- "61 / 60" would read as a mistake of ours, whoever is right.
    if record.maxLevel < record.level then
        record.maxLevel = record.level
    end
end

----------------------------------------------------------------------------
-- Distance
----------------------------------------------------------------------------

--- Add what the character travelled in `dt` seconds. `state` is what
-- Game.Read() returned: its speed is always a plain number, estimated while
-- the game hides the real one (in combat). Called on every tick, so it does
-- as little as it can.
function Journey.travel(record, state, dt)
    local speed = state.speed
    -- (Not-a-number fails the first test.)
    if speed > 0 and speed < MAX_SPEED and dt > 0 then
        local yards = speed * dt
        record.travelYd = record.travelYd + yards
        if not (state.mounted or state.taxi or state.flying or state.swimming) then
            record.footYd = record.footYd + yards
        end
    end
end

--- Character steps for a whole number of yards on foot: 2,000 to the
-- in-game mile, to the nearest step. Done in whole numbers so that the
-- companion app - which is sent the yards and does the same sum - arrives
-- at exactly the same figure.
function Journey.steps(yards)
    local scaled = floor(count(yards)) * Journey.STEPS_PER_MILE + Journey.YARDS_PER_MILE / 2
    -- (fmod is exact; Lua's % divides first, which can be one off up here.)
    return (scaled - math.fmod(scaled, Journey.YARDS_PER_MILE)) / Journey.YARDS_PER_MILE
end

----------------------------------------------------------------------------
-- Real-world totals
----------------------------------------------------------------------------
-- The walking done while playing a character is the sum of three things:
--
--   1. `record.real`: the walks that are settled. A walk settles when the
--      companion's measured numbers for it are first seen - they are taken
--      over and the addon's own record of the walk is marked `banked` - or
--      when the addon's record leaves the history list without ever having
--      been confirmed: then its estimate is taken over as it stands, and
--      `real.estimated` stays set from there on;
--   2. the addon's records of the character's walks that are still in the
--      history and not banked: estimates waiting for the companion;
--   3. the session in progress.
--
-- Each of the addon's records is in exactly one of 1 and 2, so no walk is
-- counted twice. And a settled walk depends on neither list any more: the
-- totals do not shrink when the history is pruned or cleared, nor when the
-- companion's file stops listing an old walk.

-- Add `share` of a walk's numbers to the settled totals of `record`.
local function settle(record, walk, share, estimated)
    local real = record.real
    real.walks = real.walks + 1
    real.seconds = real.seconds + count(walk.seconds) * share
    real.distanceM = real.distanceM + count(walk.distanceM) * share
    real.steps = real.steps + count(walk.steps) * share
    real.kcal = real.kcal + count(walk.kcal) * share
    if estimated then
        real.estimated = true
    end
    -- "Since" is the earliest day any of the numbers goes back to: walks
    -- from before the journey record existed count too.
    local start = usableTime(walk.start)
    if start and start < record.since then
        record.since = start
    end
end

--- Settle the walks the companion has measured.
--
-- `absorbed` is filled by SessionLog.merge(ownSessions, companionData,
-- absorbed): for each of the addon's records that is part of a measured
-- walk, that walk. Several records can be part of one measured walk (a
-- session that was restarted, or two characters played during it); they
-- share its numbers in proportion to their own time on the belt, so that
-- between them they get the walk once. `now` is when tracking begins for a
-- character that has no journey record yet.
function Journey.bank(journeys, ownSessions, absorbed, now)
    local covered, parts = {}, {}
    for index, walk in pairs(absorbed) do
        covered[walk] = (covered[walk] or 0) + count(ownSessions[index].seconds)
        parts[walk] = (parts[walk] or 0) + 1
    end
    for index, walk in pairs(absorbed) do
        local own = ownSessions[index]
        if not own.banked and Journey.isCharacter(own.character) then
            local share
            if covered[walk] > 0 then
                share = count(own.seconds) / covered[walk]
            else
                share = 1 / parts[walk]
            end
            settle(Journey.open(journeys, own.character, now), walk, share, false)
            own.banked = true
        end
    end
end

--- One of the addon's records is leaving the history list (it was pruned,
-- or the history was cleared). If the treadmill never confirmed it, its
-- estimate is settled as it stands.
function Journey.forget(journeys, own, now, written)
    if type(own) ~= "table" or own.banked or not Journey.isCharacter(own.character) then
        return
    end
    -- (A session the companion has looked for and not found was no walk:
    -- it adds nothing - see SessionLog.unconfirmed. Nor does one the addon
    -- started by itself, which estimates nothing: only a measured walk in
    -- it counts, and that is banked above.)
    if not own.auto and not ns.SessionLog.unconfirmed(own, written) then
        settle(Journey.open(journeys, own.character, now), own, 1, true)
    end
    own.banked = true
end

--- Add up the estimates of `key`'s walks that are still in the history and
-- waiting for the companion's numbers. Fills and returns `out`: walks,
-- seconds, distanceM, steps, kcal, and `first` - when the earliest of them
-- began (nil if there is none).
function Journey.unbanked(ownSessions, key, out, written)
    out.walks, out.seconds, out.distanceM, out.steps, out.kcal, out.first = 0, 0, 0, 0, 0, nil
    if type(ownSessions) ~= "table" then
        return out
    end
    local unconfirmed = ns.SessionLog.unconfirmed
    for _, own in ipairs(ownSessions) do
        -- (`written`: when the companion last wrote its file. A session
        -- that ended before then and that it has no walk for is not an
        -- estimate waiting for numbers - there was no walk.)
        if type(own) == "table" and own.character == key and not own.banked and not own.auto
            and not unconfirmed(own, written) then
            out.walks = out.walks + 1
            out.seconds = out.seconds + count(own.seconds)
            out.distanceM = out.distanceM + count(own.distanceM)
            out.steps = out.steps + count(own.steps)
            out.kcal = out.kcal + count(own.kcal)
            local start = usableTime(own.start)
            if start and (not out.first or start < out.first) then
                out.first = start
            end
        end
    end
    return out
end

--- The real-world totals of a character: its settled walks, plus `pending`
-- (what Journey.unbanked returned), plus `live` - the session in progress,
-- or nil if there is none that counts. Fills and returns `out`: walks,
-- seconds, distanceM, steps, kcal and `estimated` (true if any of it has
-- not been confirmed by the treadmill).
function Journey.totals(record, pending, live, out)
    local real = record.real
    out.walks = real.walks + pending.walks
    out.seconds = real.seconds + pending.seconds
    out.distanceM = real.distanceM + pending.distanceM
    out.steps = real.steps + pending.steps
    out.kcal = real.kcal + pending.kcal
    out.estimated = real.estimated or pending.walks > 0
    if live then
        -- Rounded down to what the session's record can hold once it is
        -- filed (whole seconds, metres and steps, tenths of a calorie), so
        -- that the totals never step back at that moment.
        out.walks = out.walks + 1
        out.seconds = out.seconds + floor(count(live.seconds))
        out.distanceM = out.distanceM + floor(count(live.distanceM))
        out.steps = out.steps + floor(count(live.steps))
        out.kcal = out.kcal + floor(count(live.kcal) * 10) / 10
        out.estimated = true
    end
    return out
end

return Journey
