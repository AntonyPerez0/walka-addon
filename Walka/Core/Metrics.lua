-- Walka metrics: calories, cadence and the in-game estimate of a walk.
--
-- Pure Lua 5.1 (no WoW API). The companion app has the same formulas in
-- companion/src/walka/metrics.py; tests/shared/metrics_vectors.json keeps the
-- two in agreement.
--
-- Why the addon estimates at all: an addon cannot receive live data from
-- outside the game, so the HUD cannot show what the treadmill really did.
-- The walker sets the belt's speed at the treadmill and tells the addon
-- what it is (the pace); from that pace the addon counts distance, steps
-- and calories while a session runs. The companion's measured totals
-- replace these estimates in the history after the next /reload or login.

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

Metrics.JOULES_PER_KCAL = 4184

-- Walking and running formulas are blended linearly across these belt
-- speeds (m/s). About half of adults still walk at 2.0 m/s (4.5 mph).
local ENERGY_BLEND_START, ENERGY_BLEND_END = 2.0, 2.2
local CADENCE_BLEND_START, CADENCE_BLEND_END = 1.9, 2.2

-- What sitting at a desk costs (Compendium of Physical Activities:
-- 1.3 MET), in watts per kg - subtracted for "extra calories versus sitting".
local SITTING_W_PER_KG = 1.3 * Metrics.JOULES_PER_KCAL / 3600

-- Per-user correction of the step estimate (counted / estimated).
Metrics.STEP_CALIBRATION_MIN = 0.7
Metrics.STEP_CALIBRATION_MAX = 1.5

local function blend(v, blendStart, blendEnd, low, high)
    if v <= blendStart then
        return low
    elseif v >= blendEnd then
        return high
    end
    local share = (v - blendStart) / (blendEnd - blendStart)
    return low + (high - low) * share
end

--- Gross metabolic rate on a moving level belt, in watts per kg of body
-- mass.
--
-- Walking: the LCDA equation (Looney et al. 2019), fitted to 48 studies and
-- valid from a standstill to 2 m/s. The textbook treadmill equation (ACSM
-- walking) reads 11-35% low at desk-walking speeds; this one was validated
-- there. Running: the ACSM running equation.
function Metrics.wattsPerKg(kmh)
    if not kmh or kmh <= 0 then
        return 0
    end
    local v = kmh / 3.6
    local walking = 1.44 + 1.94 * v ^ 0.43 + 0.24 * v ^ 4
    -- ACSM: VO2 [ml/kg/min] = 0.2 * speed [m/min] + 3.5; 1 litre O2 = 5 kcal.
    local running = (0.2 * (v * 60) + 3.5) * 5 * Metrics.JOULES_PER_KCAL / 60000
    return blend(v, ENERGY_BLEND_START, ENERGY_BLEND_END, walking, running)
end

--- Calories burned per minute at a belt speed by someone weighing
-- `weightKg`. With `net` true the cost of sitting is subtracted: what the
-- walk burned on top of sitting at the desk.
function Metrics.kcalPerMinute(kmh, weightKg, net)
    if not kmh or kmh <= 0 then
        return 0
    end
    weightKg = weightKg or Metrics.DEFAULT_PROFILE.weightKg
    local rate = Metrics.wattsPerKg(kmh)
    if net then
        rate = max(0, rate - SITTING_W_PER_KG)
    end
    return rate * weightKg * 60 / Metrics.JOULES_PER_KCAL
end

--- Estimated steps per minute at a belt speed for someone `heightCm` tall.
--
-- Walking: 128 * (speed / height)^0.465 with speed in m/s and height in m,
-- a fit to hand-counted treadmill cadence of 156 adults from 0.5 to 4.5 mph
-- (CADENCE-Adults), within 3 steps/min of the group means. Individuals
-- differ by 6-20%, most at slow speeds, hence `calibration` (counted steps
-- divided by estimated steps; 1 by default). Running: Hoeger et al. 2008.
function Metrics.stepsPerMinute(kmh, heightCm, calibration)
    if not kmh or kmh <= 0 then
        return 0
    end
    local v = kmh / 3.6
    local h = (heightCm or Metrics.DEFAULT_PROFILE.heightCm) / 100
    local walking = 128 * (v / h) ^ 0.465
    local running = 143.6 + (1084 - 531.5 * h) * v / 26.82
    calibration = min(Metrics.STEP_CALIBRATION_MAX, max(Metrics.STEP_CALIBRATION_MIN, calibration or 1))
    return blend(v, CADENCE_BLEND_START, CADENCE_BLEND_END, walking, running) * calibration
end

----------------------------------------------------------------------------
-- The estimate of a walk
----------------------------------------------------------------------------

-- The pace the addon counts with: the speed the walker says the belt is set
-- to. People settle at 1-2 mph on a treadmill desk, where typing is barely
-- affected, so that is where it starts (1.5 mph); the range takes in every
-- walking pad and a slow jog.
Metrics.DEFAULT_PACE_KMH = 2.414
Metrics.MIN_PACE_KMH = 0.5
Metrics.MAX_PACE_KMH = 10

--- Add `dt` seconds of walking at `kmh` to `totals` (a table with seconds,
-- distanceM, steps, kcal and maxKmh). `profile` supplies weightKg, heightCm
-- and optionally stepCalibration. At a pace of zero - the session is
-- paused - nothing is added.
function Metrics.walk(totals, dt, kmh, profile)
    dt = max(0, dt or 0)
    if not kmh or kmh <= 0 or dt <= 0 then
        return
    end
    profile = profile or Metrics.DEFAULT_PROFILE
    local minutes = dt / 60
    totals.seconds = (totals.seconds or 0) + dt
    totals.distanceM = (totals.distanceM or 0) + kmh / 3.6 * dt
    totals.steps = (totals.steps or 0)
        + Metrics.stepsPerMinute(kmh, profile.heightCm, profile.stepCalibration) * minutes
    totals.kcal = (totals.kcal or 0) + Metrics.kcalPerMinute(kmh, profile.weightKg) * minutes
    if kmh > (totals.maxKmh or 0) then
        totals.maxKmh = kmh
    end
end

return Metrics
