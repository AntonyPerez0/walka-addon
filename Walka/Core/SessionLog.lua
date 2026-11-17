-- Walka session log: merges the addon's estimated sessions with the
-- companion app's measured ones, and adds them up.
--
-- Pure Lua 5.1 (no WoW API beyond the standard `date`).
--
-- Two sources describe the same walks:
--   * the addon's own records (WalkaDB.sessions) - estimates, but they know
--     which character was played and how far it travelled;
--   * the companion's records (WalkaCompanionData.sessions) - measured on
--     the treadmill, and they include walks done outside the game.
-- A walk recorded by both is shown once, with the companion's numbers.

local ADDON_NAME, ns = ...
ns = ns or {}

local SessionLog = {}
ns.SessionLog = SessionLog

local date = date or os.date
local time = time or os.time
local max, min, floor = math.max, math.min, math.floor

local function number(value, fallback)
    return tonumber(value) or fallback or 0
end

local function overlapSeconds(aStart, aEnd, bStart, bEnd)
    return max(0, min(aEnd, bEnd) - max(aStart, bStart))
end

--- Do an addon record and a companion record describe the same walk?
-- True when they overlap for at least half of the shorter one.
function SessionLog.sameWalk(own, companion)
    local aStart, aEnd = number(own.start), number(own["end"])
    local bStart, bEnd = number(companion.start), number(companion["end"])
    if aEnd <= aStart or bEnd <= bStart then
        return false
    end
    local shared = overlapSeconds(aStart, aEnd, bStart, bEnd)
    local shorter = min(aEnd - aStart, bEnd - bStart)
    return shared >= shorter * 0.5
end

local function fromCompanion(record)
    return {
        start = number(record.start),
        ["end"] = number(record["end"]),
        seconds = number(record.seconds),
        distanceM = number(record.distanceM),
        steps = number(record.steps),
        kcal = number(record.kcal),
        maxKmh = number(record.maxKmh),
        avgHr = tonumber(record.avgHr),
        maxHr = tonumber(record.maxHr),
        stepsSource = record.stepsSource,
        game = record.source,
        measured = true,
    }
end

local function fromOwn(record)
    return {
        start = number(record.start),
        ["end"] = number(record["end"]),
        seconds = number(record.seconds),
        distanceM = number(record.distanceM),
        steps = number(record.steps),
        kcal = number(record.kcal),
        maxKmh = number(record.maxKmh),
        yards = tonumber(record.yards),
        character = record.character,
        measured = false,
    }
end

--- Merge both sources into one list, newest first. Each entry has: start,
-- end, seconds, distanceM, steps, kcal, maxKmh, measured (true = from the
-- treadmill), and when known: yards, character, avgHr, maxHr, stepsSource.
function SessionLog.merge(ownSessions, companionData)
    local merged = {}
    local companionSessions = companionData and companionData.sessions
    local used = {}

    if type(companionSessions) == "table" then
        for _, record in ipairs(companionSessions) do
            if type(record) == "table" and number(record["end"]) > number(record.start) then
                local entry = fromCompanion(record)
                if type(ownSessions) == "table" then
                    for index, own in ipairs(ownSessions) do
                        -- (a damaged saved-variables file can hold anything)
                        if not used[index] and type(own) == "table" and SessionLog.sameWalk(own, record) then
                            used[index] = true
                            entry.yards = (entry.yards or 0) + number(own.yards)
                            entry.character = entry.character or own.character
                        end
                    end
                end
                merged[#merged + 1] = entry
            end
        end
    end

    if type(ownSessions) == "table" then
        for index, own in ipairs(ownSessions) do
            if not used[index] and type(own) == "table" then
                merged[#merged + 1] = fromOwn(own)
            end
        end
    end

    table.sort(merged, function(a, b)
        return a.start > b.start
    end)
    return merged
end

--- Local calendar day of a timestamp, as "YYYY-MM-DD".
function SessionLog.dayKey(epoch)
    return date("%Y-%m-%d", epoch)
end

local function emptyBucket()
    return { sessions = 0, seconds = 0, distanceM = 0, steps = 0, kcal = 0, yards = 0 }
end

local function add(bucket, entry, countSession)
    if countSession then
        bucket.sessions = bucket.sessions + 1
    end
    bucket.seconds = bucket.seconds + number(entry.seconds)
    bucket.distanceM = bucket.distanceM + number(entry.distanceM)
    bucket.steps = bucket.steps + number(entry.steps)
    bucket.kcal = bucket.kcal + number(entry.kcal)
    bucket.yards = bucket.yards + number(entry.yards)
end

local SECONDS_PER_DAY = 86400

-- Noon of the local calendar day that `epoch` falls on. Whole days are
-- counted back from here rather than from `epoch` itself: a day with a
-- clock change is 23 or 25 hours long, so 86400-second steps from a time
-- near midnight would skip a calendar day or land on the same one twice.
-- From noon they always land inside the day meant.
local function noonOf(epoch)
    local fields = date("*t", epoch)
    fields.hour, fields.min, fields.sec = 12, 0, 0
    fields.isdst = nil -- let the C library decide; it may differ from `epoch`'s
    return time(fields) or epoch
end

--- Totals over a merged list (see SessionLog.merge).
--
-- Returns {
--   today, week, all = { sessions, seconds, distanceM, steps, kcal, yards },
--   days = { { day = "YYYY-MM-DD", steps, distanceM, kcal, seconds }, ... }
--          for the last 7 days, oldest first,
--   streak = number of consecutive days (ending today or yesterday) with a walk,
--   best = { steps, distanceM, seconds } - the largest single session values,
-- }
-- `live` is the session in progress (optional); `older` is the companion's
-- summary of sessions too old to be listed (optional).
function SessionLog.totals(merged, now, live, older)
    local today, week, all = emptyBucket(), emptyBucket(), emptyBucket()
    local best = { steps = 0, distanceM = 0, seconds = 0 }
    local todayKey = SessionLog.dayKey(now)
    local noon = noonOf(now)

    local dayIndex, days = {}, {}
    for offset = 6, 0, -1 do
        -- Noon avoids daylight-saving edges when stepping back whole days.
        local key = SessionLog.dayKey(noon - offset * SECONDS_PER_DAY)
        if not dayIndex[key] then
            local day = { day = key, steps = 0, distanceM = 0, kcal = 0, seconds = 0 }
            dayIndex[key] = day
            days[#days + 1] = day
        end
    end

    local walkedOn = {}
    local function account(entry, countSession)
        local key = SessionLog.dayKey(number(entry.start, now))
        add(all, entry, countSession)
        if key == todayKey then
            add(today, entry, countSession)
        end
        local day = dayIndex[key]
        if day then
            add(week, entry, countSession)
            day.steps = day.steps + number(entry.steps)
            day.distanceM = day.distanceM + number(entry.distanceM)
            day.kcal = day.kcal + number(entry.kcal)
            day.seconds = day.seconds + number(entry.seconds)
        end
        walkedOn[key] = true
        best.steps = max(best.steps, number(entry.steps))
        best.distanceM = max(best.distanceM, number(entry.distanceM))
        best.seconds = max(best.seconds, number(entry.seconds))
    end

    for _, entry in ipairs(merged or {}) do
        account(entry, true)
    end
    if live then
        account(live, true)
    end
    if type(older) == "table" then
        add(all, older, false)
        all.sessions = all.sessions + number(older.sessions)
    end

    -- Streak: count back from today (or from yesterday if today has no walk yet).
    local streak = 0
    local cursor = noon
    if not walkedOn[todayKey] then
        cursor = noon - SECONDS_PER_DAY
    end
    while walkedOn[SessionLog.dayKey(cursor)] do
        streak = streak + 1
        cursor = cursor - SECONDS_PER_DAY
        if streak > 3650 then
            break
        end
    end

    for _, bucket in ipairs({ today, week, all }) do
        bucket.steps = floor(bucket.steps + 0.5)
    end
    return { today = today, week = week, all = all, days = days, streak = streak, best = best }
end

return SessionLog
