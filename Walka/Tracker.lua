-- Walka journey tracker: the game side of Core/Journey.lua.
--
-- Keeps the journey record of the character that is logged in:
--   * how far it travels, from the state the core samples on every tick;
--   * its level, class and faction, read at login and on level-up;
--   * its time played, from the game's answer to /played, counted on from
--     there;
--   * the real-world walking done while playing it, from the session
--     history and the session in progress;
-- and hands the numbers out: to the Journey window (GetSummary) and, as
-- stat frames on the beacon, to the companion app (SendStat).
--
-- The core (Walka.lua) drives it: Load and Settle at start-up, Travel and
-- SendStat on every tick, Forget and HistoryChanged when the session
-- history changes. It runs whether or not a walking session does.

local ADDON_NAME, ns = ...

local Protocol = ns.Protocol
local SessionLog = ns.SessionLog
local Journey = ns.Journey
local Game = ns.Game
local Beacon = ns.Beacon

local Tracker = {}
ns.Tracker = Tracker

local floor = math.floor
local STAT = Protocol.STAT

local core          -- the addon's core (Walka); nil until Load
local journeys      -- WalkaDB.journeys
local key           -- "Name-Realm" of this character; nil until the game says who it is
local record        -- its journey record (nil with `key`)
local name          -- its name alone
local charKey = 0   -- what CHAR_KEY sends: a hash of `key`
local nameParts = { 0, 0, 0, 0 }    -- what NAME_0..NAME_3 send

----------------------------------------------------------------------------
-- Real-world totals
----------------------------------------------------------------------------
-- See Core/Journey.lua for what is added up. Two passes over the history
-- feed it, neither of them on a tick:
--
--   * once at start-up the history is merged with the companion's file -
--     the expensive part, and the only moment the file can have brought
--     anything new - to take over the walks it has measured;
--   * when the history changes (a session is filed, the list is cleared)
--     the character's remaining estimates are added up again, without a
--     merge.
--
-- The session in progress is added on top each time the totals are asked
-- for.

local bankStale = true      -- the start-up pass has not run yet
local pendingStale = true   -- `pending` is out of date
local pending = {}          -- the character's estimates still waiting in the history
local totals = {}           -- scratch for Journey.totals

-- A count as it is shown and sent: whole, and never rounded up - a step
-- counts when it is finished. (The sliver keeps a sum that should be
-- whole, like 2709.9999999 or forty times 0.7, from losing one.)
local function whole(value)
    return floor(value + 1e-6)
end

-- Take over the numbers of the walks the companion has measured, once
-- each, for whichever character walked them.
local function bank()
    bankStale = false
    pendingStale = true
    local sessions, absorbed = core.db.sessions, {}
    SessionLog.merge(sessions, core:GetCompanionData(), absorbed)
    Journey.bank(journeys, sessions, absorbed, Game.Now())
end

-- Add up this character's walks that are still estimates.
local function readPending()
    pendingStale = false
    Journey.unbanked(core.db.sessions, key, pending, core:GetSyncTime())
    if pending.first and pending.first < record.since then
        record.since = pending.first
    end
end

-- The session in progress, if it belongs to this character's journey: it
-- counts from the moment it is long enough to be kept, so that a start
-- that is thrown away again never shows in the totals.
local function liveSession()
    local session = core.session
    -- (One Walka started by itself estimates nothing: it is not counted
    -- until the treadmill's numbers for it arrive.)
    if session and session.character == key and not session.auto and session.seconds >= core.MIN_SESSION_SECONDS then
        return session
    end
    return nil
end

-- Fill `out` with the real-world totals as whole numbers.
local function realTotals(out)
    if bankStale then
        bank()
    end
    if pendingStale then
        readPending()
    end
    Journey.totals(record, pending, liveSession(), totals)
    out.steps = whole(totals.steps)
    out.distanceM = whole(totals.distanceM)
    out.kcal = whole(totals.kcal)
    out.seconds = whole(totals.seconds)
    out.walks = totals.walks
    out.estimated = totals.estimated
    return out
end

--- Start-up, with the history complete (a session left over from the last
-- login has been filed by now): take over the walks the companion has
-- measured.
function Tracker.Settle()
    if journeys and bankStale then
        bank()
    end
end

--- The session history has changed (a session was filed, or the list was
-- cleared): add the character's estimates up again before the totals are
-- next needed. A walk filed now is an estimate until the companion's file
-- is next loaded, so nothing needs merging.
function Tracker.HistoryChanged()
    pendingStale = true
end

--- One of the addon's session records is about to leave the history. What
-- it added to a journey stays there: the treadmill's numbers if the
-- companion has delivered them, its own estimate otherwise.
function Tracker.Forget(own)
    if not journeys then
        return
    end
    if bankStale then
        bank()
    end
    Journey.forget(journeys, own, Game.Now(), core:GetSyncTime())
    pendingStale = true
end

----------------------------------------------------------------------------
-- Time played
----------------------------------------------------------------------------
-- The game tells an addon the time played only in answer to
-- RequestTimePlayed(), and its chat window prints every answer ("Total
-- time played: ..."). In this client that print cannot be kept quiet
-- cleanly: the chat frames hand the event straight to
-- ChatFrameUtil.DisplayTimePlayed, so hiding it means replacing Blizzard's
-- function or setting a field on its frames (taint, in a client that keeps
-- chat secure for hidden values), or unregistering the event on Blizzard's
-- frames and putting it back - and this client guards event registrations
-- with "forbidden aspects": what an addon may do to Blizzard's frames
-- there could only be found out in the game, at the chat window's risk.
--
-- So Walka leaves the chat alone. It listens - the answer to the player's
-- own /played counts as well - and counts on from the last answer: time
-- logged in is time played. It asks by itself only when a walking session
-- is started and the game has not answered yet during this login, which
-- puts the game's two lines in the chat once, in reply to something the
-- player did.

local playedBase = 0    -- the total when last known, in seconds (0 = not known)
local playedAt = 0      -- GetTime() at that moment
local heard = false     -- the game has answered during this login
local askedAt           -- GetTime() of our own last question

-- A question the game has not answered is not repeated sooner than this.
local ASK_AGAIN_SECONDS = 30

--- The character's time played right now, in seconds; nil if not known yet.
function Tracker.Played()
    if playedBase <= 0 then
        return nil
    end
    return playedBase + (GetTime() - playedAt)
end

--- Ask the game for the time played, unless it has already answered during
-- this login. Returns true if it asked.
function Tracker.RequestPlayed()
    if not record or heard then
        return false
    end
    local now = GetTime()
    if askedAt and now - askedAt < ASK_AGAIN_SECONDS then
        return false
    end
    askedAt = now
    return Game.RequestTimePlayed()
end

local function onTimePlayed(total)
    total = Game.PlainNumber(total)
    if record and total and total > 0 then
        playedBase, playedAt = total, GetTime()
        heard = true
        record.played = floor(total)
    end
end

----------------------------------------------------------------------------
-- The character
----------------------------------------------------------------------------

local function readCharacter(newLevel)
    local info = Game.CharacterInfo()
    Journey.setInfo(record, newLevel or info.level, info.maxLevel, info.classId, info.faction)
end

-- Find out which character this is and open its record. The game may not
-- say yet (or may hide the name); then this is tried again later.
local function identify()
    if record then
        return true
    end
    if not journeys then
        return false    -- (not loaded yet)
    end
    local fullName = Game.CharacterName()
    if not Journey.isCharacter(fullName) then
        return false
    end
    key = fullName
    record = Journey.open(journeys, key, Game.Now())
    -- "Name-Realm": a name has no dash in it, a realm may.
    name = string.match(key, "^([^%-]+)") or key
    Protocol.nameValues(name, nameParts)
    -- The companion only needs to tell characters apart, so it gets a hash
    -- of the name and realm: the realm itself is never drawn on screen.
    charKey = Protocol.djb2(key)
    playedBase, playedAt = record.played, GetTime()
    readCharacter()
    pendingStale = true
    return true
end

-- Logging out or reloading: the saved variables are written after this.
local function save()
    if not record then
        return
    end
    local played = Tracker.Played()
    if played then
        record.played = floor(played)
    end
    record.playedHeard = heard or nil
end

--- Start tracking. `walka` is the addon's core, with its database loaded.
function Tracker.Load(walka)
    core = walka
    journeys = Journey.database(walka.db)
    identify()
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
events:RegisterEvent("PLAYER_LEVEL_UP")
events:RegisterEvent("TIME_PLAYED_MSG")
events:RegisterEvent("PLAYER_LOGOUT")
events:SetScript("OnEvent", function(_, event, first, second)
    if not core then
        return
    end
    if event == "PLAYER_ENTERING_WORLD" then
        -- (isInitialLogin, isReloadingUi) - also sent after every loading
        -- screen. By now the game knows the character's level and class.
        if identify() then
            -- A /reload is still the same login: an answer the game gave
            -- before it stands.
            if second == true and record.playedHeard then
                heard = true
            end
            record.playedHeard = nil
            readCharacter()
        end
    elseif event == "PLAYER_LEVEL_UP" then
        -- The event carries the new level; UnitLevel() may still lag.
        if record then
            readCharacter(Game.PlainNumber(first))
        end
    elseif event == "TIME_PLAYED_MSG" then
        onTimePlayed(first)
    elseif event == "PLAYER_LOGOUT" then
        save()
    end
end)

----------------------------------------------------------------------------
-- Every tick
----------------------------------------------------------------------------

--- Add what the character travelled in `dt` seconds. `state` is what
-- Game.Read() returned for this tick.
function Tracker.Travel(state, dt)
    if record then
        Journey.travel(record, state, dt)
    end
end

----------------------------------------------------------------------------
-- Stat frames
----------------------------------------------------------------------------
-- Every fifth redraw of the beacon carries one number of the journey in
-- place of the live frame (docs/PROTOCOL.md, section 4): never two in a
-- row, so the companion still hears of the session four times out of five.
-- The numbers go round in the order of Protocol.STAT_CYCLE, about 8 seconds a
-- round.

local EVERY = Protocol.STAT_EVERY
local CYCLE = Protocol.STAT_CYCLE

local redraws = 0       -- since the last stat frame
local position = 0      -- in CYCLE, of the stat sent last
local sent = {}         -- the real-world totals of the round being sent

-- The value of each stat, as a function of the moment it is sent. The
-- real-world totals are taken once per round, so that the steps, metres
-- and calories of one round belong together.
local VALUE = {
    [STAT.CHAR_KEY] = function() return charKey end,
    [STAT.NAME_0] = function() return nameParts[1] end,
    [STAT.NAME_1] = function() return nameParts[2] end,
    [STAT.NAME_2] = function() return nameParts[3] end,
    [STAT.NAME_3] = function() return nameParts[4] end,
    [STAT.CHAR_INFO] = function()
        return Protocol.charInfo(record.level, record.maxLevel, record.classId, record.faction)
    end,
    [STAT.PLAYED_S] = function() return floor(Tracker.Played() or 0) end,
    [STAT.FOOT_YD] = function() return whole(record.footYd) end,
    [STAT.TRAVEL_YD] = function() return whole(record.travelYd) end,
    [STAT.REAL_STEPS] = function() return sent.steps end,
    [STAT.REAL_M] = function() return sent.distanceM end,
    [STAT.REAL_KCAL] = function() return sent.kcal end,
    [STAT.REAL_S] = function() return sent.seconds end,
    [STAT.WALKS] = function() return sent.walks end,
    [STAT.SINCE] = function() return record.since end,
    [STAT.INFO_FLAGS] = function() return sent.estimated and Protocol.INFO_FLAG.ESTIMATED or 0 end,
}

--- Called for every redraw of the beacon with its sequence number. If this
-- redraw is a stat frame's turn, paints it and returns true: the caller
-- then leaves the live frame out. Returns false otherwise.
function Tracker.SendStat(seq)
    redraws = redraws + 1
    if redraws < EVERY then
        return false
    end
    redraws = 0
    -- No character, no journey to tell: the redraw stays a live frame.
    if not identify() then
        return false
    end
    position = position % #CYCLE + 1
    if position == 1 then
        realTotals(sent)
    end
    local statId = CYCLE[position]
    Beacon.SendStat(seq, statId, VALUE[statId]())
    return true
end

----------------------------------------------------------------------------
-- For the Journey window
----------------------------------------------------------------------------

local summary = {}

--- The character's journey as the window shows it - the same whole numbers
-- the stat frames carry. Returns nil until the game has said which
-- character this is. The table is reused between calls.
--
--   name, level, maxLevel, classId, faction, since
--   played           seconds, or nil if the game has not said yet
--   footYd, travelYd whole yards
--   characterSteps   on-foot yards as steps (Journey.steps)
--   steps, distanceM, kcal, seconds, walks
--                    the real-world walking done while playing it
--   estimated        true if those include numbers the treadmill has not
--                    confirmed
function Tracker.GetSummary()
    if not identify() then
        return nil
    end
    realTotals(summary)
    local played = Tracker.Played()
    summary.name = name
    summary.level, summary.maxLevel = record.level, record.maxLevel
    summary.classId, summary.faction = record.classId, record.faction
    summary.since = record.since
    summary.played = played and floor(played) or nil
    summary.footYd = whole(record.footYd)
    summary.travelYd = whole(record.travelYd)
    summary.characterSteps = Journey.steps(summary.footYd)
    return summary
end

--- The journey record of this character (nil until it is known), and its
-- "Name-Realm".
function Tracker.GetRecord()
    return record, key
end

--- "Name-Realm" of this character as it was read when the game first gave
-- it - so it is still known while the game hides names. Until then, what
-- the game says now (see Game.CharacterName).
function Tracker.CharacterName()
    if identify() then
        return key
    end
    return Game.CharacterName()
end

return Tracker
