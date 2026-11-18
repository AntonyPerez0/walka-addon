-- Walka: links an under-desk walking pad to your character.
--
-- This file is the addon's core: saved settings, the walking session
-- (start / pause / resume / stop), and the ten-times-a-second tick that
-- reads the character's state, runs the speed model and paints the beacon
-- for the companion app. The windows live in UI/; the character's journey
-- is kept by Tracker.lua, which this file drives.

local ADDON_NAME, ns = ...

local Protocol = ns.Protocol
local SpeedModel = ns.SpeedModel
local Metrics = ns.Metrics
local SessionLog = ns.SessionLog
local Game = ns.Game
local Beacon = ns.Beacon
local Tracker = ns.Tracker
local RoadRecorder = ns.RoadRecorder

local Walka = {}
ns.Walka = Walka
_G.Walka = Walka

local FLAG = Protocol.FLAG
local KMH_PER_MPH = Metrics.KMH_PER_MPH

Walka.PREFIX = "|cff4fc3f7Walka|r: "
Walka.TICK_SECONDS = 0.1      -- game state sampling + beacon refresh
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
    local units = defaultUnits()
    local speed = {}
    for key, value in pairs(SpeedModel.DEFAULTS) do
        speed[key] = value
    end
    return {
        units = units,
        speed = speed,
        profile = {
            weightKg = Metrics.DEFAULT_PROFILE.weightKg,
            heightCm = Metrics.DEFAULT_PROFILE.heightCm,
        },
        beacon = { enabled = true, cellPx = Beacon.DEFAULT_CELL_PX },
        hud = { shown = true, locked = false, scale = 1.0, alpha = 0.9 },
        autoPauseAFK = true,
        recordRoad = true,      -- keep the character's path for the map
        dailyStepGoal = 6000,
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

local function copyTable(source)
    local copy = {}
    for key, value in pairs(source) do
        if type(value) == "table" then
            copy[key] = copyTable(value)
        else
            copy[key] = value
        end
    end
    return copy
end

----------------------------------------------------------------------------
-- Callbacks (the UI subscribes to these)
----------------------------------------------------------------------------
-- Events: "TICK" (live numbers changed), "SESSION" (started/paused/resumed/
-- stopped), "SETTINGS" (a setting changed), "HISTORY" (the session list
-- changed).

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
-- before they are used: a height of 0 or a speed step of 0 would otherwise
-- turn into inf/NaN in the estimates, the beacon and the saved variables.

-- `value` as a number within low..high, or nil. (NaN fails both tests.)
local function numberWithin(value, low, high)
    value = tonumber(value)
    if value and value >= low and value <= high then
        return value
    end
    return nil
end

-- `value` as a positive, finite number, or nil.
local function positiveNumber(value)
    return numberWithin(value, 1e-9, 1e9)
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

--- What the treadmill can do, in km/h: { minKmh, maxKmh, stepKmh, ... }.
-- Taken from the companion when it has talked to the treadmill, otherwise
-- assumed from the display unit (UREVO US pads: 0.6-4.0 mph in 0.1 mph).
function Walka:GetBeltLimits()
    local data = self:GetCompanionData()
    local limits = data and data.limits
    if type(limits) == "table" then
        local minKmh, maxKmh = positiveNumber(limits.minKmh), positiveNumber(limits.maxKmh)
        if minKmh and maxKmh and maxKmh >= minKmh then
            return {
                minKmh = minKmh,
                maxKmh = maxKmh,
                stepKmh = positiveNumber(limits.stepKmh) or 0.1,
                accelKmhPerS = positiveNumber(limits.accelKmhPerS),
                decelKmhPerS = positiveNumber(limits.decelKmhPerS),
                fromCompanion = true,
            }
        end
    end
    if self.db.settings.units == "mph" then
        return { minKmh = 0.6 * KMH_PER_MPH, maxKmh = 4.0 * KMH_PER_MPH, stepKmh = 0.1 * KMH_PER_MPH }
    end
    return { minKmh = 1.0, maxKmh = 6.0, stepKmh = 0.1 }
end

----------------------------------------------------------------------------
-- Derived settings
----------------------------------------------------------------------------

--- Rebuild everything that depends on settings. Call after changing any.
function Walka:ApplySettings()
    local settings = self.db.settings
    local limits = self:GetBeltLimits()

    -- The speed model reads this table live.
    local model = self.modelSettings
    for key in pairs(model) do
        model[key] = nil
    end
    for key, value in pairs(settings.speed) do
        model[key] = value
    end
    model.minBeltKmh = limits.minKmh
    model.stepKmh = limits.stepKmh
    if model.maxKmh > limits.maxKmh then
        model.maxKmh = limits.maxKmh
    end

    local estimator = self.estimatorLimits
    estimator.minBeltKmh = limits.minKmh
    estimator.accelKmhPerS = limits.accelKmhPerS
    estimator.decelKmhPerS = limits.decelKmhPerS

    Beacon.SetCellSize(settings.beacon.cellPx)
    settings.beacon.cellPx = Beacon.GetCellSize()
    Beacon.SetVisible(settings.beacon.enabled)
    self:Fire("SETTINGS")
end

--- Change one setting by dotted path, e.g. Walka:Set("speed.runKmh", 3.2).
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

--- Turn "keep walking" on or off: with it on, the belt holds the walking
-- pace whenever the character stands still, instead of slowing down.
function Walka:SetKeepWalking(enabled)
    enabled = enabled and true or false
    self:Set("speed.keepWalking", enabled)
    if self.db.settings.announce then
        if enabled then
            self:Print("keep walking is on - the belt holds your walking pace ("
                .. self:FormatSpeed(self.db.settings.speed.walkKmh) .. ") when your character stops.")
        else
            self:Print("keep walking is off - the belt slows down when your character stops.")
        end
    end
end

--- Restore every speed setting to its default.
function Walka:ResetSpeedSettings()
    self.db.settings.speed = copyTable(SpeedModel.DEFAULTS)
    self:ApplySettings()
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

--- Estimated belt speed right now (km/h) and the target the addon is asking for.
function Walka:GetLiveSpeed()
    return self.estimator.beltKmh, self.targetKmh
end

--- The character state sampled on the last tick (see Game.Read).
function Walka:GetGameState()
    return self.gameState
end

-- The windows redraw on the "SESSION" event, which comes before the next
-- tick. Work out now what that tick is going to ask for, so that they do
-- not show a stopped belt ("waiting") for a moment after every start and
-- resume. A model update over zero seconds does exactly that.
local function primeTarget(self)
    self.targetKmh = self.model:update(0, self.gameState)
end

function Walka:StartSession()
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
    }
    self.model:reset(nil)
    self.estimator:reset(0)
    primeTarget(self)
    if db.settings.announce then
        self:Print("session started - the belt follows your character. /walka stop to finish.")
    end
    self:Fire("SESSION", "start")
end

function Walka:PauseSession(reason)
    local session = self.session
    if not session then
        return
    end
    if session.paused then
        -- Pausing by hand during an AFK pause makes the pause the player's
        -- own: the belt must not restart by itself when AFK ends.
        if (reason or "user") == "user" and session.pauseReason ~= "user" then
            session.pauseReason = "user"
            self:Fire("SESSION", "pause")
        end
        return
    end
    session.paused = true
    session.pauseReason = reason or "user"
    self.targetKmh = 0
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
    session.afkStill = nil
    -- The belt restarts at its minimum speed.
    self.model:reset(nil)
    primeTarget(self)
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
    self.targetKmh = 0
    self.estimator:reset(0)
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
        if self.db.settings.announce then
            self:Print(string.format(
                "session finished: %s, %s, %s steps, %d kcal (estimates - /reload to load the companion's numbers).",
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

local beaconFrame = { seq = 0, session = 0, flags = 0, target = 0, charSpeed = 0 }

local function stateFlags(state)
    local flags = 0
    if state.moving then flags = flags + FLAG.MOVING end
    if state.mounted then flags = flags + FLAG.MOUNTED end
    if state.taxi then flags = flags + FLAG.TAXI end
    if state.swimming then flags = flags + FLAG.SWIMMING end
    if state.flying then flags = flags + FLAG.FLYING end
    if state.dead then flags = flags + FLAG.DEAD end
    if state.combat then flags = flags + FLAG.COMBAT end
    if state.afk then flags = flags + FLAG.AFK end
    if state.falling then flags = flags + FLAG.FALLING end
    if state.resting then flags = flags + FLAG.RESTING end
    if state.estimated then flags = flags + FLAG.ESTIMATED end
    return flags
end

--- Advance everything by `dt` seconds. Called ten times a second.
function Walka:Tick(dt)
    local state = Game.Read()
    self.gameState = state
    -- The journey counts every yard, with or without a session.
    Tracker.Travel(state, dt)
    RoadRecorder.Watchdog()
    local session = self.session
    local flags = stateFlags(state)
    local target = 0

    if session then
        if session.paused and session.pauseReason == "afk" then
            -- Back from AFK: the session resumes when the character *starts*
            -- moving - it stood still on the tick before, and moves now -
            -- not on the first key press: typing a chat line should not
            -- start a belt. A character that is being carried along anyway
            -- (a flight path, auto-run, /follow) pressed no key for anyone:
            -- it has to stand first, and a flight path never counts. (Also
            -- when the automatic pause has been switched off meanwhile: this
            -- pause was automatic, so is its end.)
            if not state.afk and state.moving and session.afkStill and not state.taxi then
                self:ResumeSession()
            else
                session.afkStill = not state.moving
            end
        elseif self.db.settings.autoPauseAFK and state.afk and not session.paused then
            self:PauseSession("afk")
        end

        flags = flags + FLAG.ACTIVE
        if session.paused then
            flags = flags + FLAG.PAUSED
            -- On a paused frame the AFK flag says *why* it is paused: set
            -- for the automatic pause and only for it. The companion takes
            -- a pause without it for one the player asked for (which
            -- acknowledges a stop made at the treadmill) - also when the
            -- player pauses by hand while the game still shows them AFK.
            local automatic = session.pauseReason == "afk"
            if automatic and not state.afk then
                flags = flags + FLAG.AFK
            elseif state.afk and not automatic then
                flags = flags - FLAG.AFK
            end
        else
            target = self.model:update(dt, state)
            session.yards = session.yards + state.speed * dt
        end
        self.estimator:update(dt, target, session, self:GetProfile())
    end
    self.targetKmh = target

    beaconFrame.seq = (beaconFrame.seq + 1) % 256
    -- Every fifth redraw shows one number of the character's journey in
    -- place of the live frame (docs/PROTOCOL.md, section 4). Everything
    -- above has run as on any other tick; only the picture differs.
    if Tracker.SendStat(beaconFrame.seq) then
        return
    end
    beaconFrame.session = session and session.id or 0
    beaconFrame.flags = flags
    beaconFrame.target = Protocol.kmhToRaw(target)
    beaconFrame.charSpeed = Protocol.ypsToRaw(state.speed)
    Beacon.Send(beaconFrame)
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
    -- would raise on every tick, with the beacon frozen meanwhile).
    session.maxKmh = tonumber(session.maxKmh) or 0
    session.paused = session.paused and true or false
    if session.pauseReason ~= "afk" and session.pauseReason ~= "user" then
        session.pauseReason = session.paused and "user" or nil
    end
    session.afkStill = nil  -- (who was standing before the reload proves nothing now)
    local savedAt = tonumber(saved.savedAt) or 0
    if Game.Now() - savedAt <= Walka.RESUME_WINDOW_SECONDS then
        self.session = session
        self.model:reset(tonumber(saved.targetKmh))
        self.estimator:reset(tonumber(saved.beltKmh) or 0)
        self:Fire("SESSION", "restore")
    else
        self.session = session
        self:StopSession(savedAt)
    end
end

local function saveSession(self)
    if not self.session then
        self.db.active = nil
        return
    end
    self.db.active = {
        savedAt = Game.Now(),
        session = self.session,
        beltKmh = self.estimator.beltKmh,
        targetKmh = self.targetKmh,
    }
end

function Walka:OnLoad()
    self.db = initDatabase()
    self.modelSettings = {}
    self.estimatorLimits = {}
    self.model = SpeedModel.new(self.modelSettings)
    self.estimator = Metrics.Estimator.new(self.estimatorLimits)
    self.targetKmh = 0
    self.gameState = Game.Read()
    -- Before a saved session is restored: closing an old one files it in
    -- the history, which the tracker has to hear about.
    Tracker.Load(self)

    Beacon.Create()
    self:ApplySettings()
    restoreSession(self)
    Tracker.Settle()
    -- The road recorder runs on its own timer; nothing it does can reach
    -- the tick below.
    pcall(RoadRecorder.Load, self)
    Beacon.GetFrame():SetScript("OnUpdate", onUpdate)
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
    "/walka - show or hide the HUD",
    "/walka start | pause | resume | stop - control the walking session",
    "/walka history - past sessions and totals",
    "/walka journey - your character's journey so far: level, time played, your steps and its steps",
    "/walka options - settings",
    "/walka status - what the addon is doing right now",
    "/walka weight 180lb (or 82kg) - your weight, for calorie estimates",
    "/walka height 5'11 (or 180cm) - your height, for step estimates",
    "/walka keepwalking on | off - hold the walking pace when your character stops",
    "/walka units mph | kmh",
    "/walka beacon on | off | size <2-8>",
}

local function slash(message)
    local command, rest = string.match(message or "", "^%s*(%S*)%s*(.-)%s*$")
    command = string.lower(command or "")
    local self = Walka
    if not self.loaded then
        return
    end

    if command == "" or command == "hud" or command == "show" or command == "hide" then
        if ns.HUD then
            if command == "show" then
                ns.HUD.SetShown(true)
            elseif command == "hide" then
                ns.HUD.SetShown(false)
            else
                ns.HUD.Toggle()
            end
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
    elseif command == "history" or command == "log" then
        if ns.History then
            ns.History.Toggle()
        end
    elseif command == "journey" or command == "card" then
        if ns.JourneyWindow then
            ns.JourneyWindow.Toggle()
        end
    elseif command == "options" or command == "config" or command == "settings" then
        if ns.Options then
            ns.Options.Open()
        end
    elseif command == "status" then
        local state = self.gameState
        local belt, target = self:GetLiveSpeed()
        self:Print(string.format(
            "session %s | character %.1f yd/s%s | target %s | belt (est.) %s",
            self.session and (self.session.paused and "paused" or "active") or "off",
            state.speed,
            state.estimated and " (estimated: hidden in combat)" or "",
            self:FormatSpeed(target),
            self:FormatSpeed(belt)
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
    elseif command == "keepwalking" or command == "keep" then
        local arg = string.lower(rest)
        if arg == "" then
            self:SetKeepWalking(not self:Get("speed.keepWalking"))
        elseif arg == "on" or arg == "off" then
            self:SetKeepWalking(arg == "on")
        else
            self:Print("usage: /walka keepwalking on  or  /walka keepwalking off")
        end
    elseif command == "units" then
        rest = string.lower(rest)
        if rest == "mph" or rest == "kmh" or rest == "km/h" then
            self:Set("units", rest == "mph" and "mph" or "kmh")
            self:Print("speeds are now shown in " .. self:SpeedUnit() .. ".")
        else
            self:Print("usage: /walka units mph  or  /walka units kmh")
        end
    elseif command == "beacon" then
        local arg, value = string.match(string.lower(rest), "^(%S*)%s*(%S*)")
        if arg == "on" or arg == "off" then
            self:Set("beacon.enabled", arg == "on")
            self:Print("beacon " .. arg .. ".")
        elseif arg == "size" and tonumber(value) then
            self:Set("beacon.cellPx", tonumber(value))
            self:Print("beacon cell size is now " .. Beacon.GetCellSize() .. " px.")
        else
            self:Print("usage: /walka beacon on | off | size <2-8>")
        end
    else
        for index = 1, #HELP do
            self:Print(HELP[index])
        end
    end
end

SLASH_WALKA1 = "/walka"
SlashCmdList["WALKA"] = slash

--- Addon compartment (minimap dropdown) click.
function Walka_OnCompartmentClick(_, button)
    if not Walka.loaded then
        return
    end
    if button == "RightButton" and ns.Options then
        ns.Options.Open()
    elseif ns.HUD then
        ns.HUD.Toggle()
    end
end

return Walka
