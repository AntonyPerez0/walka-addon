-- Walka metrics: calories, cadence and the in-game belt estimate.
--
-- Pure Lua 5.1 (no WoW API). The companion app has the same formulas in
-- companion/src/walka/metrics.py; tests/shared/metrics_vectors.json keeps the
-- two in agreement.
--
-- Why the addon estimates at all: an addon cannot receive live data from
-- outside the game, so the HUD cannot show what the treadmill really did.
-- But the addon decides the belt's target speed, and the companion follows
-- it with known rate limits - so replaying those limits here gives a belt
-- speed, and from it distance, steps and calories, that track the real
-- numbers closely. The companion's measured totals replace these estimates
-- in the history after the next /reload or login.

local ADDON_NAME, ns = ...
ns = ns or {}

local Metrics = {}
ns.Metrics = Metrics

local max, min = math.max, math.min

Metrics.KMH_PER_MPH = 1.609344
Metrics.METERS_PER_MILE = 1609.344
Metrics.METERS_PER_YARD = 0.9144

--- Default body profile, used until the user (or the companion) sets one.
Metrics.DEFAULT_PROFILE = {
    weightKg = 75,
    heightCm = 172,
}

-- Speed (km/h) above which the running equations apply.
Metrics.RUN_THRESHOLD_KMH = 8.0

-- Resting oxygen uptake, ml O2 per kg per minute (1 MET).
local VO2_REST = 3.5
-- kcal released per litre of oxygen.
local KCAL_PER_LITRE_O2 = 5.0

--- Gross oxygen uptake while walking/running on a level belt,
-- in ml O2 per kg per minute. `heightM` is the walker's height in metres.
function Metrics.vo2(kmh, heightM)
    if not kmh or kmh <= 0 then
        return VO2_REST
    end
    if kmh >= Metrics.RUN_THRESHOLD_KMH then
        -- ACSM running equation (level): 0.2 ml/kg/min per m/min + rest.
        return VO2_REST + 0.2 * (kmh * 1000 / 60)
    end
    -- Ludlow & Weyand (2016) height-weight-speed walking model: rest + a
    -- minimum walking cost + a term in speed squared over height. Unlike
    -- the ACSM walking equation it stays accurate at slow desk-walking speeds.
    local v = kmh / 3.6
    return VO2_REST + 3.85 + 5.97 * v * v / heightM
end

--- Calories burned per minute at a belt speed.
-- With `net` true, resting metabolism is subtracted ("active calories").
function Metrics.kcalPerMinute(kmh, weightKg, heightCm, net)
    if not kmh or kmh <= 0 then
        return 0
    end
    weightKg = weightKg or Metrics.DEFAULT_PROFILE.weightKg
    local heightM = (heightCm or Metrics.DEFAULT_PROFILE.heightCm) / 100
    local vo2 = Metrics.vo2(kmh, heightM)
    if net then
        vo2 = vo2 - VO2_REST
    end
    return vo2 * weightKg / 1000 * KCAL_PER_LITRE_O2
end

--- Steps per minute at a belt speed for someone `heightCm` tall.
function Metrics.stepsPerMinute(kmh, heightCm)
    if not kmh or kmh <= 0 then
        return 0
    end
    local heightM = (heightCm or Metrics.DEFAULT_PROFILE.heightCm) / 100
    if kmh >= Metrics.RUN_THRESHOLD_KMH then
        -- Running cadence rises slowly with speed.
        return (150 + 2.2 * (kmh - Metrics.RUN_THRESHOLD_KMH)) * (1.70 / heightM) ^ 0.5
    end
    -- Walking: cadence grows with speed^0.427 and shrinks with height^0.71
    -- (dynamic similarity), anchored at 93 steps/min at 2 mph and 125 at
    -- 4 mph for a 1.70 m adult.
    return 56.5 * kmh ^ 0.427 * (1.70 / heightM) ^ 0.71
end

----------------------------------------------------------------------------
-- Belt estimator
----------------------------------------------------------------------------

local Estimator = {}
Estimator.__index = Estimator
Metrics.Estimator = Estimator

--- How the companion and the treadmill move the belt.
Estimator.DEFAULTS = {
    accelKmhPerS = 0.5,   -- companion's limit on speeding up
    decelKmhPerS = 1.0,   -- ... and on slowing down
    stopKmhPerS = 2.5,    -- belt coasting to a halt when paused/stopped
    countdownSeconds = 3, -- treadmill's countdown before the belt starts
    minBeltKmh = 1.0,     -- speed the belt starts at
}

--- `limits` may override any of Estimator.DEFAULTS and is read live.
function Estimator.new(limits)
    local self = setmetatable({}, Estimator)
    self.limits = limits or {}
    self:reset(0)
    return self
end

function Estimator:reset(beltKmh)
    self.beltKmh = beltKmh or 0
    self.countdown = 0
end

local function limitOf(self, key)
    local value = self.limits[key]
    if value == nil then
        return Estimator.DEFAULTS[key]
    end
    return value
end

--- Advance by `dt` seconds towards `targetKmh` (0 = belt stopped) and add
-- what was walked to `totals` (a table with seconds, distanceM, steps, kcal).
-- `profile` supplies weightKg and heightCm. Returns the estimated belt speed.
function Estimator:update(dt, targetKmh, totals, profile)
    dt = max(0, dt or 0)
    targetKmh = targetKmh or 0
    local belt = self.beltKmh

    if targetKmh <= 0 then
        self.countdown = 0
        belt = max(0, belt - limitOf(self, "stopKmhPerS") * dt)
    elseif belt <= 0 then
        -- Starting from rest: countdown, then the belt appears at minimum.
        if self.countdown <= 0 then
            self.countdown = limitOf(self, "countdownSeconds")
        end
        self.countdown = self.countdown - dt
        if self.countdown <= 0 then
            self.countdown = 0
            belt = min(targetKmh, limitOf(self, "minBeltKmh"))
        end
    elseif belt < targetKmh then
        belt = min(targetKmh, belt + limitOf(self, "accelKmhPerS") * dt)
    elseif belt > targetKmh then
        belt = max(targetKmh, belt - limitOf(self, "decelKmhPerS") * dt)
    end
    self.beltKmh = belt

    if belt > 0 and totals then
        profile = profile or Metrics.DEFAULT_PROFILE
        local minutes = dt / 60
        totals.seconds = (totals.seconds or 0) + dt
        totals.distanceM = (totals.distanceM or 0) + belt / 3.6 * dt
        totals.steps = (totals.steps or 0) + Metrics.stepsPerMinute(belt, profile.heightCm) * minutes
        totals.kcal = (totals.kcal or 0) + Metrics.kcalPerMinute(belt, profile.weightKg, profile.heightCm) * minutes
        if belt > (totals.maxKmh or 0) then
            totals.maxKmh = belt
        end
    end
    return belt
end

return Metrics
