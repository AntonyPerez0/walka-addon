-- Walka speed model: turns the character's movement into a belt-speed target.
--
-- Pure Lua 5.1 (no WoW API) so it can be unit-tested outside the game.
--
-- A WoW character runs at 7 yd/s (23 km/h), which nobody wants under a desk,
-- so the mapping is a user-tunable curve rather than a 1:1 match:
--
--   character speed (yd/s):   0        2.5       7         14+
--   belt speed (km/h):        stand -> walk  ->  run  ->   mount
--
-- with straight lines between the anchor points. Speed buffs and slows land
-- between the anchors, so Sprint really does make you walk faster.
--
-- The raw curve output is then smoothed (the character changes speed
-- instantly, a belt must not), briefly held when the character stops (looting,
-- talking to an NPC), quantised to the treadmill's speed step, and clamped.

local ADDON_NAME, ns = ...
ns = ns or {}

local SpeedModel = {}
SpeedModel.__index = SpeedModel
ns.SpeedModel = SpeedModel

local exp, floor, abs, max, min = math.exp, math.floor, math.abs, math.max, math.min

-- Character speeds (yards/second) that anchor the curve.
SpeedModel.WALK_YPS = 2.5
SpeedModel.RUN_YPS = 7.0
SpeedModel.MOUNT_YPS = 14.0

SpeedModel.KMH_PER_MPH = 1.609344

-- Tolerance when comparing a speed with the cap, in km/h: half the
-- resolution of the beacon's target field (0.01 km/h). Settings arrive
-- rounded - the options page stores 0.001 km/h, so a cap of "1.0 mph" is
-- 1.609 rather than 1.609344 - and without this the largest allowed grid
-- value would come out a whole step (0.1 mph) below what the user chose.
SpeedModel.CAP_TOLERANCE_KMH = 0.005

--- Default settings. All belt speeds are km/h.
SpeedModel.DEFAULTS = {
    -- Paces in the range people settle at on treadmill desks (1-2 mph),
    -- where typing is barely affected; the options page raises them.
    standKmh = 1.287, -- character standing still (0.8 mph). 0 = stop the belt.
    walkKmh = 1.931,  -- character walking, 2.5 yd/s (1.2 mph)
    runKmh = 2.575,   -- character running, 7 yd/s (1.6 mph)
    mountKmh = 3.219, -- 100% mount speed and above, 14 yd/s (2.0 mph)
    maxKmh = 4.023,   -- never ask for more than this (2.5 mph)
    minBeltKmh = 1.0, -- slowest speed the treadmill can hold; below it = stop
    stepKmh = 0.1,    -- treadmill speed increment
    riseSeconds = 4,  -- smoothing time constant when speeding up
    fallSeconds = 8,  -- smoothing time constant when slowing down
    stopHoldSeconds = 4, -- keep pace this long after the character stops
    taxiMode = "run", -- on flight paths: "run", "stand" or "follow"
    combatHold = false, -- in combat never drop below the running pace
    keepWalking = false, -- never drop below the walking pace: when the
                         -- character stops, the belt stays at walkKmh
}

local function settingOf(settings, key)
    local value = settings and settings[key]
    if value == nil then
        return SpeedModel.DEFAULTS[key]
    end
    return value
end

--- Belt speed (km/h) the curve assigns to a character speed (yd/s),
-- before smoothing and clamping.
function SpeedModel.curve(settings, yps)
    local stand = settingOf(settings, "standKmh")
    local walk = settingOf(settings, "walkKmh")
    local run = settingOf(settings, "runKmh")
    local mount = settingOf(settings, "mountKmh")
    yps = max(0, yps or 0)

    local kmh
    if yps <= 0 then
        kmh = stand
    elseif yps < SpeedModel.WALK_YPS then
        kmh = stand + (walk - stand) * (yps / SpeedModel.WALK_YPS)
    elseif yps < SpeedModel.RUN_YPS then
        kmh = walk + (run - walk) * ((yps - SpeedModel.WALK_YPS) / (SpeedModel.RUN_YPS - SpeedModel.WALK_YPS))
    elseif yps < SpeedModel.MOUNT_YPS then
        kmh = run + (mount - run) * ((yps - SpeedModel.RUN_YPS) / (SpeedModel.MOUNT_YPS - SpeedModel.RUN_YPS))
    else
        kmh = mount
    end

    -- "Keep walking": standing still (looting, casting, reading a quest)
    -- should not cost steps, so the walking pace becomes the floor.
    if settingOf(settings, "keepWalking") and kmh < walk then
        kmh = walk
    end
    return kmh
end

--- Create a model. `settings` is read on every update, so the options panel
-- can change values live; missing keys fall back to SpeedModel.DEFAULTS.
function SpeedModel.new(settings)
    local self = setmetatable({}, SpeedModel)
    self.settings = settings or {}
    self:reset(nil)
    return self
end

--- Forget all history. `kmh` seeds the smoothed speed (default: the pace
-- for a character standing still).
function SpeedModel:reset(kmh)
    self.smoothed = kmh or SpeedModel.curve(self.settings, 0)
    self.output = nil
    self.lastYps = 0
    self.holdLeft = 0
    self.holdRaw = nil
    self.effectiveYps = 0
end

--- Character speed after applying the state rules (taxi, falling, combat).
function SpeedModel:effectiveSpeed(input)
    local settings = self.settings
    local yps = max(0, input.speed or 0)

    if input.taxi then
        local mode = settingOf(settings, "taxiMode")
        if mode == "run" then
            return SpeedModel.RUN_YPS
        elseif mode == "stand" then
            return 0
        end
        return yps
    end

    if input.falling then
        -- Falling/jumping says nothing about intent: keep whatever we had.
        return self.effectiveYps
    end

    if input.combat and settingOf(settings, "combatHold") and yps < SpeedModel.RUN_YPS then
        return SpeedModel.RUN_YPS
    end
    return yps
end

local function quantise(value, step)
    if step <= 0 then
        return value
    end
    return floor(value / step + 0.5) * step
end

--- Advance the model by `dt` seconds.
--
-- input = { speed = yd/s, taxi, falling, combat } (booleans may be nil)
--
-- Returns the belt-speed target in km/h: either 0 (stop the belt) or a value
-- within [minBeltKmh, maxKmh] on the treadmill's speed grid.
function SpeedModel:update(dt, input)
    local settings = self.settings
    dt = max(0, dt or 0)

    local yps = self:effectiveSpeed(input)
    self.effectiveYps = yps
    local raw = SpeedModel.curve(settings, yps)

    -- Stop hold: when the character comes to a halt, keep the pace we had
    -- for a few seconds so that looting or talking does not slow the belt.
    if yps <= 0 then
        if self.lastYps > 0 then
            self.holdLeft = settingOf(settings, "stopHoldSeconds")
            self.holdRaw = SpeedModel.curve(settings, self.lastYps)
        end
        if self.holdLeft > 0 and self.holdRaw then
            self.holdLeft = self.holdLeft - dt
            if self.holdLeft > 0 then
                raw = max(raw, self.holdRaw)
            end
        end
    else
        self.holdLeft = 0
        self.holdRaw = nil
    end
    self.lastYps = yps

    local maxKmh = settingOf(settings, "maxKmh")
    raw = min(raw, maxKmh)

    -- Asymmetric exponential smoothing.
    local tau = raw > self.smoothed and settingOf(settings, "riseSeconds") or settingOf(settings, "fallSeconds")
    if tau <= 0 then
        self.smoothed = raw
    elseif dt > 0 then
        self.smoothed = self.smoothed + (raw - self.smoothed) * (1 - exp(-dt / tau))
    end

    -- Quantise with hysteresis so the target does not flicker between steps.
    local step = settingOf(settings, "stepKmh")
    local output = self.output
    if output == nil or abs(self.smoothed - output) >= step * 0.75 then
        output = quantise(self.smoothed, step)
    end
    -- Snap onto the destination once we are within half a step of it,
    -- otherwise the hysteresis could leave us parked one step short forever.
    if abs(self.smoothed - raw) < step * 0.5 then
        output = quantise(raw, step)
    end

    local capped = maxKmh + SpeedModel.CAP_TOLERANCE_KMH
    if output > capped then
        -- Largest grid value that does not exceed the cap.
        output = floor(capped / step) * step
    end

    local minBelt = settingOf(settings, "minBeltKmh")
    if output < minBelt then
        if raw <= 0 then
            -- Heading for a stop (stand speed 0): once the smoothed speed
            -- falls below what the belt can do, stop it.
            output = 0
        else
            -- Smallest grid value the belt can hold.
            output = floor(minBelt / step + 1 - 1e-6) * step
        end
    end

    -- The cap is the safety limit, so it wins over the belt's minimum: if
    -- the slowest speed the belt can hold is above the cap (or the speed
    -- step is nonsense and the arithmetic above gave NaN), stop.
    if output ~= output or output > capped then
        output = 0
    end

    self.output = output
    return output
end

--- Convenience conversions for the UI.
function SpeedModel.kmhToMph(kmh)
    return kmh / SpeedModel.KMH_PER_MPH
end

function SpeedModel.mphToKmh(mph)
    return mph * SpeedModel.KMH_PER_MPH
end

return SpeedModel
