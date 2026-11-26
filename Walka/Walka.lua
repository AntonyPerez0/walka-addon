-- Walka: links an under-desk walking pad to your character.
--
-- This file is the addon's core: saved settings, the walking session
-- (start / pause / resume / stop), and the ten-times-a-second tick that
-- reads the character's state and counts the walk. The character's journey
-- is kept by Tracker.lua, which this file drives.
--
-- The addon draws nothing on the screen: it is the game's end of the
-- connection to the Walka app, which shows everything - on its dashboard,
-- and in a floating window it can put on top of the game. The two never
-- talk while the game runs. The addon keeps its sessions in its saved
-- variables, which the game writes to a file at logout and on /reload, and
-- the app reads that file to know which character each walk was for
-- (docs/PROTOCOL.md). In game there are a few slash commands and Walka's
-- entry in the game's AddOns menu by the minimap (click: start or pause
-- the session; right-click: stop it).
--
-- Walka only counts. The walker sets the belt's speed at the treadmill;
-- nothing here - and nothing in the companion app - starts, stops or
-- steers a belt.

local ADDON_NAME, ns = ...

local Metrics = ns.Metrics
local SessionLog = ns.SessionLog
local Game = ns.Game
local Tracker = ns.Tracker
local RoadRecorder = ns.RoadRecorder

local Walka = {}
ns.Walka = Walka
_G.Walka = Walka

local KMH_PER_MPH = Metrics.KMH_PER_MPH

Walka.PREFIX = "|cff4fc3f7Walka|r: "
Walka.TICK_SECONDS = 0.1      -- game state sampling
Walka.UI_SECONDS = 0.25       -- how often the windows are told to redraw
Walka.MIN_SESSION_SECONDS = 20 -- shorter sessions are not kept
Walka.RESUME_WINDOW_SECONDS = 180 -- a session survives a /reload this long
Walka.MAX_SESSIONS = 400

----------------------------------------------------------------------------
-- Settings
----------------------------------------------------------------------------

local function defaultUnits()
    local locale = GetLocale and GetLocale() or "enUS"
    return locale == "enUS" and "mph" or "kmh"
end

--- Defaults for WalkaDB.settings. Speeds are km/h whatever the display unit.
local function buildDefaults()
    return {
        units = defaultUnits(),
        -- The speed the walker says the belt is set to: what the live
        -- estimate counts with (see Metrics.walk).
        paceKmh = Metrics.DEFAULT_PACE_KMH,
        profile = {
            weightKg = Metrics.DEFAULT_PROFILE.weightKg,
            heightCm = Metrics.DEFAULT_PROFILE.heightCm,
        },
        autoPauseAFK = true,
        -- Start the session by itself once the character is in the game,
        -- so that nobody has to type a command: see Walka:StartAutoSession.
        autoSession = true,
        recordRoad = true,      -- keep the character's path, for the app's map
        announce = true,
    }
end

local function applyDefaults(target, defaults)
    for key, value in pairs(defaults) do
        if type(value) == "table" then
            if type(target[key]) ~= "table" then
                target[key] = {}
            end
            applyDefaults(target[key], value)
        elseif type(target[key]) ~= type(value) then
            -- Missing, or of the wrong type (a hand-edited or damaged
            -- saved-variables file): the code does arithmetic on these.
            target[key] = value
        end
    end
    return target
end

----------------------------------------------------------------------------
-- Callbacks (the UI subscribes to these)
----------------------------------------------------------------------------
-- Events: "TICK" (live numbers changed), "SESSION" (started/paused/resumed/
-- stopped), "SETTINGS" (a setting changed), "HISTORY" (the session list
-- changed), "ROAD_MAPS" (the road is on a land whose map was not known).

local callbacks = {}

function Walka:On(event, fn)
    callbacks[event] = callbacks[event] or {}
    table.insert(callbacks[event], fn)
end

function Walka:Fire(event, ...)
    local list = callbacks[event]
    if not list then
        return
    end
    for index = 1, #list do
        local ok, err = pcall(list[index], ...)
        if not ok and geterrorhandler then
            geterrorhandler()(err)
        end
    end
end

function Walka:Print(message)
    print(Walka.PREFIX .. tostring(message))
end

----------------------------------------------------------------------------
-- Companion data (written into CompanionData.lua by the companion app and
-- picked up on login or /reload)
----------------------------------------------------------------------------

-- The data file is written by another program, so its numbers are checked
-- before they are used: a height of 0 would otherwise turn into inf/NaN
-- in the estimates and the saved variables.

-- `value` as a number within low..high, or nil. (NaN fails both tests.)
local function numberWithin(value, low, high)
    value = tonumber(value)
    if value and value >= low and value <= high then
        return value
    end
    return nil
end

--- The table the companion wrote, or nil.
function Walka:GetCompanionData()
    local data = _G.WalkaCompanionData
    if type(data) == "table" then
        return data
    end
    return nil
end

--- When the companion wrote its data (seconds since the epoch), or nil if
-- it has not - or if the timestamp is not one date() can format, so that
-- callers can pass the result straight to date(). (On Windows date()
-- returns nil for a time before 1970 or after the year 3000, and
-- `"..." .. nil` is an error.)
function Walka:GetSyncTime()
    local data = self:GetCompanionData()
    return data and numberWithin(data.written, 0, SessionLog.MAX_EPOCH - 1)
end

-- The companion's profile as GetProfile returns it. One table, refilled on
-- every call: the tick asks ten times a second.
local companionProfile = { fromCompanion = true }

--- Body profile used for the live estimates: the companion's if it has
-- one (so both sides compute the same), otherwise the addon's own.
-- Returns a table that is reused between calls; copy what you need to keep.
function Walka:GetProfile()
    local data = self:GetCompanionData()
    local own = self.db.settings.profile
    if data and type(data.profile) == "table" then
        -- The same plausible ranges as /walka weight and /walka height.
        local weightKg = numberWithin(data.profile.weightKg, 30, 250)
        if weightKg then
            companionProfile.weightKg = weightKg
            companionProfile.heightCm = numberWithin(data.profile.heightCm, 120, 230) or own.heightCm
            companionProfile.stepCalibration = numberWithin(
                data.profile.stepCalibration,
                Metrics.STEP_CALIBRATION_MIN,
                Metrics.STEP_CALIBRATION_MAX
            )
            return companionProfile
        end
    end
    return own
end

----------------------------------------------------------------------------
-- Derived settings
----------------------------------------------------------------------------

--- Rebuild everything that depends on settings. Call after changing any.
function Walka:ApplySettings()
    self:Fire("SETTINGS")
end

--- Change one setting by dotted path, e.g. Walka:Set("profile.weightKg", 80).
function Walka:Set(path, value)
    local node = self.db.settings
    local last
    for part in string.gmatch(path, "[^%.]+") do
        if last then
            if type(node[last]) ~= "table" then
                node[last] = {}
            end
            node = node[last]
        end
        last = part
    end
    node[last] = value
    self:ApplySettings()
end

--- Read one setting by dotted path.
function Walka:Get(path)
    local node = self.db.settings
    for part in string.gmatch(path, "[^%.]+") do
        if type(node) ~= "table" then
            return nil
        end
        node = node[part]
    end
    return node
end

--- The pace the live estimate counts with, in km/h: the speed the walker
-- says the belt is set to. (Checked on the way out as well as on the way
-- in: Walka:Set can put anything into the settings, and the tick does
-- arithmetic with this.)
function Walka:GetPace()
    return numberWithin(self.db.settings.paceKmh, Metrics.MIN_PACE_KMH, Metrics.MAX_PACE_KMH)
        or Metrics.DEFAULT_PACE_KMH
end

--- Set the pace, in km/h. It is kept within what Metrics takes for a
-- walking pace and stored to a thousandth, so that "1.5 mph" comes back as
-- 1.5 mph. Returns the pace as it now is - unchanged, if `kmh` is no speed
-- at all.
function Walka:SetPace(kmh)
    kmh = tonumber(kmh)
    if not kmh or kmh ~= kmh then
        return self:GetPace()
    end
    kmh = math.min(Metrics.MAX_PACE_KMH, math.max(Metrics.MIN_PACE_KMH, kmh))
    kmh = math.floor(kmh * 1000 + 0.5) / 1000
    self:Set("paceKmh", kmh)
    return kmh
end

----------------------------------------------------------------------------
-- Formatting helpers (shared by the windows)
----------------------------------------------------------------------------

function Walka:IsMph()
    return self.db.settings.units == "mph"
end

--- "2.0 mph" / "3.2 km/h"
function Walka:FormatSpeed(kmh, bare)
    kmh = kmh or 0
    if self:IsMph() then
        local text = string.format("%.1f", kmh / KMH_PER_MPH)
        return bare and text or (text .. " mph")
    end
    local text = string.format("%.1f", kmh)
    return bare and text or (text .. " km/h")
end

function Walka:SpeedUnit()
    return self:IsMph() and "mph" or "km/h"
end

--- Convert a speed in the display unit to km/h and back.
function Walka:ToKmh(displaySpeed)
    return self:IsMph() and displaySpeed * KMH_PER_MPH or displaySpeed
end

function Walka:FromKmh(kmh)
    return self:IsMph() and kmh / KMH_PER_MPH or kmh
end

--- "0.52 mi" / "0.84 km"
function Walka:FormatDistance(meters)
    meters = meters or 0
    if self:IsMph() then
        return string.format("%.2f mi", meters / Metrics.METERS_PER_MILE)
    end
    return string.format("%.2f km", meters / 1000)
end

--- "23:45" or "1:23:45"
function Walka:FormatDuration(seconds)
    seconds = math.floor((seconds or 0) + 0.5)
    local hours = math.floor(seconds / 3600)
    local minutes = math.floor(seconds % 3600 / 60)
    local secs = seconds % 60
    if hours > 0 then
        return string.format("%d:%02d:%02d", hours, minutes, secs)
    end
    return string.format("%d:%02d", minutes, secs)
end

--- 12345 -> "12,345"
function Walka:FormatNumber(value)
    local text = tostring(math.floor((value or 0) + 0.5))
    local formatted, count = text, 0
    repeat
        formatted, count = string.gsub(formatted, "^(-?%d+)(%d%d%d)", "%1,%2")
    until count == 0
    return formatted
end

----------------------------------------------------------------------------
-- Session
----------------------------------------------------------------------------

--- The session in progress, or nil. Fields: id, start, seconds, distanceM,
-- steps, kcal, yards, maxKmh, paused, pauseReason, character.
function Walka:GetSession()
    return self.session
end

function Walka:IsActive()
    return self.session ~= nil
end

function Walka:IsPaused()
    return self.session ~= nil and self.session.paused == true
end

--- The belt speed the estimate is counting with right now, in km/h: the
-- pace while a session is running, 0 while it is paused or there is none.
function Walka:GetLiveSpeed()
    local session = self.session
    if session and not session.paused and not session.auto then
        return self:GetPace()
    end
    return 0
end

--- The character state sampled on the last tick (see Game.Read).
function Walka:GetGameState()
    return self.gameState
end

function Walka:StartSession(auto)
    if self.session then
        if self.session.paused then
            self:ResumeSession()
        end
        return
    end
    local db = self.db
    db.sessionCounter = (db.sessionCounter or 0) % 255 + 1
    self.session = {
        id = db.sessionCounter,
        start = Game.Now(),
        seconds = 0,
        distanceM = 0,
        steps = 0,
        kcal = 0,
        yards = 0,
        maxKmh = 0,
        paused = false,
        -- (The name as read at login, should the game be hiding it now:
        -- the walk is this character's either way.)
        character = Tracker.CharacterName(),
        -- Started by itself (Walka:StartAutoSession): it only says whose
        -- walks are walked now, and estimates nothing.
        auto = auto and true or nil,
    }
    if auto then
        if db.settings.announce then
            self:Print("Walka is on - walks on your walking pad count for this character "
                .. "(the Walka app sees which when you log out or /reload). "
                .. "Click Walka in the AddOns menu to pause.")
        end
        self:Fire("SESSION", "start")
        -- The Share picture's time played: asked for once if this
        -- character's is not known yet (it is counted on from there).
        if db.settings.announce and not Tracker.Played() then
            Tracker.RequestPlayed()
        end
        return
    end
    if db.settings.announce then
        self:Print(string.format(
            "session started - your walk counts for this character. Start the belt at the treadmill; "
                .. "the Walka app shows the walk (until its numbers arrive, Walka estimates at %s). "
                .. "/walka stop to finish.",
            self:FormatSpeed(self:GetPace())
        ))
    end
    self:Fire("SESSION", "start")
    -- The app's Share picture shows the character's time played, which the
    -- game tells only when asked (and prints in the chat when it answers).
    -- Asked here, once a login at most: in reply to something the player did
    -- (and not with announcements off: they keep the chat quiet).
    if db.settings.announce then
        Tracker.RequestPlayed()
    end
end

--- The session Walka starts by itself, once a login, as soon as the
-- character is known: the walk the app records while it runs is filed
-- under this character, with nobody typing a command. It makes no estimate
-- of its own (it cannot know whether anybody is on the pad): only the
-- treadmill's measured numbers count, once they arrive. A session the
-- player stops stays stopped until the next login or /reload.
function Walka:StartAutoSession()
    self.autoTried = true
    if self.session or not self.db.settings.autoSession then
        return
    end
    local name = Tracker.CharacterName()
    if not ns.Journey.isCharacter(name) then
        self.autoTried = false  -- the game has not said who this is yet: next tick
        return
    end
    self:StartSession(true)
end

function Walka:PauseSession(reason)
    local session = self.session
    if not session then
        return
    end
    if session.paused then
        -- Pausing by hand during an AFK pause makes the pause the player's
        -- own: it must not end by itself when AFK does.
        if (reason or "user") == "user" and session.pauseReason ~= "user" then
            session.pauseReason = "user"
            self:Fire("SESSION", "pause")
        end
        return
    end
    session.paused = true
    session.pauseReason = reason or "user"
    if self.db.settings.announce then
        if reason == "afk" then
            self:Print("you are AFK - session paused. It resumes when your character moves.")
        else
            self:Print("session paused.")
        end
    end
    self:Fire("SESSION", "pause")
end

function Walka:ResumeSession()
    local session = self.session
    if not session or not session.paused then
        return
    end
    session.paused = false
    session.pauseReason = nil
    if self.db.settings.announce then
        self:Print("session resumed.")
    end
    self:Fire("SESSION", "resume")
end

function Walka:TogglePause()
    if not self.session then
        self:StartSession()
    elseif self.session.paused then
        self:ResumeSession()
    else
        self:PauseSession("user")
    end
end

local function finishRecord(session, endTime)
    return {
        id = session.id,
        start = session.start,
        ["end"] = endTime,
        seconds = math.floor(session.seconds + 0.5),
        distanceM = math.floor(session.distanceM + 0.5),
        steps = math.floor(session.steps + 0.5),
        kcal = math.floor(session.kcal * 10 + 0.5) / 10,
        yards = math.floor(session.yards + 0.5),
        maxKmh = math.floor((session.maxKmh or 0) * 100 + 0.5) / 100,
        character = session.character,
        auto = session.auto,
        -- What of the app's measured walks it has taken over while it ran
        -- (Tracker: a /reload brings them): not to be counted again.
        counted = session.counted,
        banked = session.banked,
    }
end

--- End the session and file it in the history. Returns the record, or nil
-- if the session was too short to keep.
function Walka:StopSession(endTime)
    local session = self.session
    if not session then
        return nil
    end
    self.session = nil
    local record
    if session.seconds >= Walka.MIN_SESSION_SECONDS then
        record = finishRecord(session, endTime or Game.Now())
        local sessions = self.db.sessions
        table.insert(sessions, record)
        while #sessions > Walka.MAX_SESSIONS do
            -- (The character's journey keeps what the walk added to it.)
            Tracker.Forget(table.remove(sessions, 1))
        end
        Tracker.HistoryChanged()
        if self.db.settings.announce and record.auto then
            self:Print("session finished - the Walka app has the treadmill's numbers.")
        elseif self.db.settings.announce then
            self:Print(string.format(
                "session finished: %s, %s, %s steps, %d kcal (estimates - the Walka app has the treadmill's numbers).",
                self:FormatDuration(record.seconds),
                self:FormatDistance(record.distanceM),
                self:FormatNumber(record.steps),
                math.floor(record.kcal + 0.5)
            ))
        end
        self:Fire("HISTORY")
    elseif self.db.settings.announce then
        self:Print("session ended (too short to keep).")
    end
    self:Fire("SESSION", "stop", record)
    return record
end

--- Sessions for the history window: the companion's measured sessions
-- merged with the addon's own estimates, newest first.
function Walka:GetHistory()
    return SessionLog.merge(self.db.sessions, self:GetCompanionData())
end

--- Totals for today / the last 7 days / all time (see SessionLog.totals).
-- The session in progress is included. Merging the history is the
-- expensive part: a caller that already holds the list from GetHistory()
-- passes it as `history` instead of having it merged a second time.
function Walka:GetTotals(history)
    local data = self:GetCompanionData()
    return SessionLog.totals(history or self:GetHistory(), Game.Now(), self.session, data and data.older)
end

--- Forget the addon's own session history (the companion's is untouched,
-- and so is what the walks added to each character's journey).
function Walka:ClearHistory()
    for _, record in ipairs(self.db.sessions) do
        Tracker.Forget(record)
    end
    self.db.sessions = {}
    Tracker.HistoryChanged()
    self:Fire("HISTORY")
end

----------------------------------------------------------------------------
-- The tick
----------------------------------------------------------------------------

--- Advance everything by `dt` seconds. Called ten times a second.
function Walka:Tick(dt)
    local state = Game.Read()
    self.gameState = state
    -- The journey counts every yard, with or without a session.
    Tracker.Travel(state, dt)
    RoadRecorder.Watchdog()
    if self.restoredAt then
        self:CloseRestoredSessionOfAnother()
    end
    if not self.autoTried then
        self:StartAutoSession()
    end
    local session = self.session

    if session then
        if session.paused and session.pauseReason == "afk" then
            -- Back from AFK: the session carries on when the character
            -- moves again. (Also when the automatic pause has been switched
            -- off meanwhile: this pause was automatic, so is its end.)
            if not state.afk and state.moving then
                self:ResumeSession()
            end
        elseif self.db.settings.autoPauseAFK and state.afk and not session.paused then
            self:PauseSession("afk")
        end

        if not session.paused then
            session.yards = session.yards + state.speed * dt
            if session.auto then
                -- Time only: whether anybody walked is the treadmill's to say.
                session.seconds = session.seconds + dt
            else
                Metrics.walk(session, dt, self:GetPace(), self:GetProfile())
            end
        end
    end
end

local tickElapsed, uiElapsed = 0, 0

local function onUpdate(_, elapsed)
    tickElapsed = tickElapsed + elapsed
    if tickElapsed < Walka.TICK_SECONDS then
        return
    end
    local dt = tickElapsed
    tickElapsed = 0
    -- After a hitch or loading screen, do not integrate one giant step.
    if dt > 1 then
        dt = 1
    end
    Walka:Tick(dt)

    uiElapsed = uiElapsed + dt
    if uiElapsed >= Walka.UI_SECONDS then
        uiElapsed = 0
        Walka:Fire("TICK")
    end
end

----------------------------------------------------------------------------
-- Start-up, reload and logout
----------------------------------------------------------------------------

local function initDatabase()
    if type(WalkaDB) ~= "table" then
        WalkaDB = {}
    end
    local db = WalkaDB
    db.version = 1
    db.settings = applyDefaults(type(db.settings) == "table" and db.settings or {}, buildDefaults())
    -- Until 0.7 the addon asked the companion for belt speeds and kept a
    -- page of settings for that. Nothing reads them any more.
    db.settings.speed = nil
    db.riskNoticeShown = nil
    -- Until 0.9 the addon had windows of its own: a HUD, the history, the
    -- journey, the road on the world map. The app shows all of that now.
    db.settings.hud = nil
    db.settings.worldMap = nil
    db.settings.dailyStepGoal = nil
    -- Until 0.11 the addon drew a strip of squares in the corner of the
    -- screen for the app to read. The app reads the saved variables now.
    db.settings.beacon = nil
    db.settings.paceKmh = numberWithin(db.settings.paceKmh, Metrics.MIN_PACE_KMH, Metrics.MAX_PACE_KMH)
        or Metrics.DEFAULT_PACE_KMH
    -- The body profile feeds every step and calorie: a height of zero from a
    -- damaged or hand-edited file would put inf into the saved sessions,
    -- and the game cannot read such a file back.
    local profile = db.settings.profile
    profile.weightKg = numberWithin(profile.weightKg, 30, 250) or Metrics.DEFAULT_PROFILE.weightKg
    profile.heightCm = numberWithin(profile.heightCm, 120, 230) or Metrics.DEFAULT_PROFILE.heightCm
    if type(db.sessions) ~= "table" then
        db.sessions = {}
    end
    db.sessionCounter = tonumber(db.sessionCounter) or 0
    return db
end

-- A session in progress is saved at logout/reload and picked up again if
-- the UI comes back quickly; otherwise it is closed at the time it was saved.
local function restoreSession(self)
    local saved = self.db.active
    self.db.active = nil
    if type(saved) ~= "table" or type(saved.session) ~= "table" then
        return
    end
    local session = saved.session
    -- Every tick does arithmetic on these: a session from a damaged file
    -- is dropped rather than resumed.
    for _, key in ipairs({ "id", "start", "seconds", "distanceM", "steps", "kcal", "yards" }) do
        if type(session[key]) ~= "number" then
            return
        end
    end
    -- The rest is made sound rather than checked (a top speed that is text
    -- would raise on every tick, and stop the count).
    session.maxKmh = tonumber(session.maxKmh) or 0
    session.paused = session.paused and true or false
    if session.pauseReason ~= "afk" and session.pauseReason ~= "user" then
        session.pauseReason = session.paused and "user" or nil
    end
    -- (Kept by older versions for a rule about restarting the belt.)
    session.afkStill, session.pausedAt = nil, nil
    session.auto = session.auto == true or nil
    local savedAt = tonumber(saved.savedAt) or 0
    self.session = session
    if Game.Now() - savedAt > Walka.RESUME_WINDOW_SECONDS then
        self:StopSession(savedAt)
        return
    end
    -- WalkaDB is the account's, and so is the session saved in it: logging
    -- out of one character and in on another within the window must not
    -- carry the first one's session over. Checked again on the tick while
    -- the game has not said yet who is logged in.
    self.restoredAt = savedAt
    if not self:CloseRestoredSessionOfAnother() then
        self:Fire("SESSION", "restore")
    end
end

--- A session picked up after a logout belongs to the character that saved
-- it. If another character has logged in meanwhile, the session is closed
-- at the time it was saved (and this character starts its own), so that
-- nothing walked in this login is filed under the other one. True if it
-- was closed.
function Walka:CloseRestoredSessionOfAnother()
    local savedAt, session = self.restoredAt, self.session
    if not savedAt or not session then
        self.restoredAt = nil
        return false
    end
    local now = Tracker.CharacterName()
    if not ns.Journey.isCharacter(now) then
        return false    -- not known yet: the next tick asks again
    end
    self.restoredAt = nil
    if ns.Journey.isCharacter(session.character) and session.character ~= now then
        self:StopSession(savedAt)
        return true
    end
    return false
end

local function saveSession(self)
    if not self.session then
        self.db.active = nil
        return
    end
    self.db.active = {
        savedAt = Game.Now(),
        session = self.session,
    }
end

function Walka:OnLoad()
    self.db = initDatabase()
    self.gameState = Game.Read()
    -- Before a saved session is restored: closing an old one files it in
    -- the history, which the tracker has to hear about.
    Tracker.Load(self)

    self:ApplySettings()
    restoreSession(self)
    Tracker.Settle()
    -- The road recorder runs on its own timer; nothing it does can reach
    -- the tick below.
    pcall(RoadRecorder.Load, self)
    -- The tick runs from a frame of its own. It has no size and draws
    -- nothing: a frame that is shown gets OnUpdate on every redraw.
    local ticker = CreateFrame("Frame", "WalkaTickFrame", nil)
    ticker:SetScript("OnUpdate", onUpdate)
    self.ticker = ticker
    self.loaded = true
    self:Fire("LOADED")
end

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGOUT")
events:SetScript("OnEvent", function(_, event, name)
    if event == "ADDON_LOADED" then
        if name == ADDON_NAME then
            events:UnregisterEvent("ADDON_LOADED")
            Walka:OnLoad()
        end
    elseif event == "PLAYER_LOGOUT" then
        if Walka.loaded then
            saveSession(Walka)
        end
    end
end)

----------------------------------------------------------------------------
-- Slash commands
----------------------------------------------------------------------------

local function parseMeasure(text, units)
    -- "180", "180lb", "82 kg", "5'11", "71in", "180cm"
    text = string.lower(text or "")
    local feet, inches = string.match(text, "^(%d+)'%s*(%d*)")
    if feet then
        return (tonumber(feet) * 12 + (tonumber(inches) or 0)), "in"
    end
    local number, unit = string.match(text, "^([%d%.]+)%s*(%a*)")
    number = tonumber(number)
    if not number then
        return nil
    end
    if unit == "" then
        unit = units
    end
    return number, unit
end

local HELP = {
    "/walka start | pause | resume | stop - the walking session: your walk counts for this character",
    "/walka status - what the addon is doing right now",
    "/walka pace 1.5 - the speed your treadmill is set to, for the estimate until the app's numbers arrive",
    "/walka road on | off - record your character's road for the app's map, or stop",
    "/walka auto on | off - start the session by itself at login (on), or only when asked",
    "/walka weight 180lb (or 82kg) - your weight, for calorie estimates",
    "/walka height 5'11 (or 180cm) - your height, for step estimates",
    "/walka units mph | kmh",
}

local function slash(message)
    local command, rest = string.match(message or "", "^%s*(%S*)%s*(.-)%s*$")
    command = string.lower(command or "")
    local self = Walka
    if not self.loaded then
        return
    end

    if command == "" or command == "help" then
        self:Print("connects the game to the Walka app, which shows your walks - its dashboard, "
            .. "and a floating window over the game (app: Settings -> Floating window).")
        for index = 1, #HELP do
            self:Print(HELP[index])
        end
    elseif command == "start" or command == "go" then
        self:StartSession()
    elseif command == "pause" then
        self:PauseSession("user")
    elseif command == "resume" then
        self:ResumeSession()
    elseif command == "stop" or command == "end" then
        if not self.session then
            self:Print("no session is running.")
        else
            self:StopSession()
        end
    elseif command == "hud" or command == "show" or command == "hide" or command == "history" or command == "log"
        or command == "journey" or command == "card" or command == "map" or command == "options"
        or command == "config" or command == "settings" then
        -- The windows these opened are gone: the app shows all of it.
        self:Print("the addon has no windows any more: your walks, history, journey and road are in the "
            .. "Walka app. For the walk on top of the game: Settings -> Floating window, in the app.")
    elseif command == "auto" then
        if rest == "on" or rest == "off" then
            self:Set("autoSession", rest == "on")
            if rest == "on" then
                self:Print("Walka starts the session by itself when you log in.")
                self:StartAutoSession()
            else
                self:Print("Walka no longer starts the session by itself: click Walka in the AddOns menu, "
                    .. "or /walka start, when you walk.")
            end
        else
            self:Print("use /walka auto on or /walka auto off.")
        end
    elseif command == "road" then
        if rest == "on" or rest == "off" then
            self:Set("recordRoad", rest == "on")
            RoadRecorder.SetEnabled(rest == "on")
            self:Print(rest == "on" and "recording your character's road." or
                "no longer recording your character's road (what is recorded so far is kept).")
        else
            self:Print("use /walka road on or /walka road off. The road is shown in the Walka app (Share).")
        end
    elseif command == "status" then
        local state = self.gameState
        self:Print(string.format(
            "session %s | character %.1f yd/s%s | counting at %s (your pace: %s)",
            self.session and (self.session.paused and "paused" or "active") or "off",
            state.speed,
            state.estimated and " (estimated: hidden in combat)" or "",
            self:FormatSpeed(self:GetLiveSpeed()),
            self:FormatSpeed(self:GetPace())
        ))
        local synced = self:GetSyncTime()
        if synced then
            self:Print("companion data from " .. date("%Y-%m-%d %H:%M", synced) .. ".")
        else
            self:Print("no companion data yet - run the Walka companion app, then /reload.")
        end
    elseif command == "weight" then
        local value, unit = parseMeasure(rest, self:IsMph() and "lb" or "kg")
        if not value then
            self:Print("usage: /walka weight 180lb  or  /walka weight 82kg")
            return
        end
        if unit == "lb" or unit == "lbs" then
            value = value * 0.45359237
        elseif unit ~= "kg" and unit ~= "kgs" then
            -- "180 pounds", "12 stone" or a height typed by mistake: do not
            -- read a unit we do not know as kilograms.
            self:Print("usage: /walka weight 180lb  or  /walka weight 82kg")
            return
        end
        if value < 30 or value > 250 then
            self:Print("that weight looks wrong - use e.g. 180lb or 82kg.")
            return
        end
        self:Set("profile.weightKg", math.floor(value * 10 + 0.5) / 10)
        self:Print(string.format("weight set to %.1f kg (%.0f lb).", value, value / 0.45359237))
        if self:GetProfile().fromCompanion then
            self:Print("note: your body profile comes from the companion app (Settings -> Body profile), "
                .. "so this value is only used when the companion's file is not there.")
        end
    elseif command == "height" then
        local value, unit = parseMeasure(rest, self:IsMph() and "in" or "cm")
        if not value then
            self:Print("usage: /walka height 5'11  or  /walka height 180cm")
            return
        end
        if unit == "in" or unit == "inch" or unit == "inches" then
            value = value * 2.54
        elseif unit == "m" then
            value = value * 100
        elseif unit ~= "cm" then
            -- As for the weight: an unknown unit is not centimetres.
            self:Print("usage: /walka height 5'11  or  /walka height 180cm")
            return
        end
        if value < 120 or value > 230 then
            self:Print("that height looks wrong - use e.g. 5'11 or 180cm.")
            return
        end
        self:Set("profile.heightCm", math.floor(value + 0.5))
        self:Print(string.format("height set to %d cm.", math.floor(value + 0.5)))
        if self:GetProfile().fromCompanion then
            self:Print("note: your body profile comes from the companion app (Settings -> Body profile), "
                .. "so this value is only used when the companion's file is not there.")
        end
    elseif command == "pace" then
        if rest == "" then
            self:Print("your pace is " .. self:FormatSpeed(self:GetPace())
                .. " - what Walka counts your walk with. Change it with /walka pace <speed>, "
                .. "to what your treadmill is set to.")
            return
        end
        local value, unit = parseMeasure(rest, self:IsMph() and "mph" or "kmh")
        local kmh
        if value and unit == "mph" then
            kmh = value * KMH_PER_MPH
        elseif value and (unit == "kmh" or unit == "km") then    -- ("km/h" is read up to the slash)
            kmh = value
        end
        if not kmh then
            self:Print("usage: /walka pace 1.5  (in " .. self:SpeedUnit() .. "), or /walka pace 1.5mph, /walka pace 2.4kmh")
            return
        end
        if kmh < Metrics.MIN_PACE_KMH or kmh > Metrics.MAX_PACE_KMH then
            self:Print(string.format(
                "that pace looks wrong - Walka counts with %s to %s.",
                self:FormatSpeed(Metrics.MIN_PACE_KMH),
                self:FormatSpeed(Metrics.MAX_PACE_KMH)
            ))
            return
        end
        self:Print("pace set to " .. self:FormatSpeed(self:SetPace(kmh))
            .. ". (This is what Walka counts with; the belt's speed is set at the treadmill.)")
    elseif command == "units" then
        rest = string.lower(rest)
        if rest == "mph" or rest == "kmh" or rest == "km/h" then
            self:Set("units", rest == "mph" and "mph" or "kmh")
            self:Print("speeds are now shown in " .. self:SpeedUnit() .. ".")
        else
            self:Print("usage: /walka units mph  or  /walka units kmh")
        end
    elseif command == "beacon" then
        -- (Until 0.11: the strip of squares the app read off the screen.)
        self:Print("Walka no longer draws anything on the screen: the app reads your sessions from "
            .. "the game's saved file when you log out or /reload.")
    else
        for index = 1, #HELP do
            self:Print(HELP[index])
        end
    end
end

SLASH_WALKA1 = "/walka"
SlashCmdList["WALKA"] = slash

--- Walka in the game's AddOns menu (by the minimap): a click starts the
-- session, or pauses or resumes it; a right-click stops it.
function Walka_OnCompartmentClick(_, button)
    if not Walka.loaded then
        return
    end
    if button == "RightButton" then
        if Walka.session then
            Walka:StopSession()
        else
            Walka:Print("no session is running - click Walka here to start one.")
        end
        return
    end
    Walka:TogglePause()
end

return Walka
