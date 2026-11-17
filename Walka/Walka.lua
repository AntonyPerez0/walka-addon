-- Walka: links an under-desk walking pad to your character.
--
-- This file is the addon's core: saved settings, the walking session
-- (start / pause / resume / stop), and the ten-times-a-second tick that
-- reads the character's state, runs the speed model and paints the beacon
-- for the companion app. The windows live in UI/.

local ADDON_NAME, ns = ...

local Protocol = ns.Protocol
local SpeedModel = ns.SpeedModel
local Metrics = ns.Metrics
local SessionLog = ns.SessionLog
local Game = ns.Game
local Beacon = ns.Beacon

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
        netCalories = false,
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
        elseif target[key] == nil then
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

--- The table the companion wrote, or nil.
function Walka:GetCompanionData()
    local data = _G.WalkaCompanionData
    if type(data) == "table" then
        return data
    end
    return nil
end

--- Body profile used for the live estimates: the companion's if it has
-- one (so both sides compute the same), otherwise the addon's own.
function Walka:GetProfile()
    local data = self:GetCompanionData()
    local own = self.db.settings.profile
    if data and type(data.profile) == "table" and tonumber(data.profile.weightKg) then
        return {
            weightKg = tonumber(data.profile.weightKg),
            heightCm = tonumber(data.profile.heightCm) or own.heightCm,
            fromCompanion = true,
        }
    end
    return own
end

--- What the treadmill can do, in km/h: { minKmh, maxKmh, stepKmh, ... }.
-- Taken from the companion when it has talked to the treadmill, otherwise
-- assumed from the display unit (UREVO US pads: 0.6-4.0 mph in 0.1 mph).
function Walka:GetBeltLimits()
    local data = self:GetCompanionData()
    local limits = data and data.limits
    if type(limits) == "table" and tonumber(limits.minKmh) and tonumber(limits.maxKmh) then
        return {
            minKmh = tonumber(limits.minKmh),
            maxKmh = tonumber(limits.maxKmh),
            stepKmh = tonumber(limits.stepKmh) or 0.1,
            accelKmhPerS = tonumber(limits.accelKmhPerS),
            decelKmhPerS = tonumber(limits.decelKmhPerS),
            fromCompanion = true,
        }
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
        character = Game.CharacterName(),
    }
    self.model:reset(nil)
    self.estimator:reset(0)
    self.targetKmh = 0
    if db.settings.announce then
        self:Print("session started - the belt follows your character. /walka stop to finish.")
    end
    self:Fire("SESSION", "start")
end

function Walka:PauseSession(reason)
    local session = self.session
    if not session or session.paused then
        return
    end
    session.paused = true
    session.pauseReason = reason or "user"
    self.targetKmh = 0
    if self.db.settings.announce then
        if reason == "afk" then
            self:Print("you are AFK - session paused.")
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
    -- The belt restarts at its minimum speed.
    self.model:reset(nil)
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
            table.remove(sessions, 1)
        end
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
-- The session in progress is included.
function Walka:GetTotals()
    local data = self:GetCompanionData()
    return SessionLog.totals(self:GetHistory(), Game.Now(), self.session, data and data.older)
end

--- Forget the addon's own session history (the companion's is untouched).
function Walka:ClearHistory()
    self.db.sessions = {}
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
    local session = self.session
    local flags = stateFlags(state)
    local target = 0

    if session then
        if self.db.settings.autoPauseAFK then
            if state.afk and not session.paused then
                self:PauseSession("afk")
            elseif not state.afk and session.paused and session.pauseReason == "afk" then
                self:ResumeSession()
            end
        end

        flags = flags + FLAG.ACTIVE
        if session.paused then
            flags = flags + FLAG.PAUSED
        else
            target = self.model:update(dt, state)
            session.yards = session.yards + state.speed * dt
        end
        self.estimator:update(dt, target, session, self:GetProfile())
    end
    self.targetKmh = target

    beaconFrame.seq = (beaconFrame.seq + 1) % 256
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

    Beacon.Create()
    self:ApplySettings()
    restoreSession(self)
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
    "/walka options - settings",
    "/walka status - what the addon is doing right now",
    "/walka weight 180lb (or 82kg) - your weight, for calorie estimates",
    "/walka height 5'11 (or 180cm) - your height, for step estimates",
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
        local data = self:GetCompanionData()
        if data and tonumber(data.written) then
            self:Print("companion data from " .. date("%Y-%m-%d %H:%M", tonumber(data.written)) .. ".")
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
        end
        if value < 30 or value > 250 then
            self:Print("that weight looks wrong - use e.g. 180lb or 82kg.")
            return
        end
        self:Set("profile.weightKg", math.floor(value * 10 + 0.5) / 10)
        self:Print(string.format("weight set to %.1f kg (%.0f lb).", value, value / 0.45359237))
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
        end
        if value < 120 or value > 230 then
            self:Print("that height looks wrong - use e.g. 5'11 or 180cm.")
            return
        end
        self:Set("profile.heightCm", math.floor(value + 0.5))
        self:Print(string.format("height set to %d cm.", math.floor(value + 0.5)))
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
