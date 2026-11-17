-- Walka game-state reader: everything the addon asks the WoW client.
--
-- WoW: Forever runs on the modern (Midnight) client, which hides some values
-- from addons during combat as "secret values": GetUnitSpeed() still returns
-- something, but any arithmetic or comparison on it raises a Lua error. So
-- this file is the only place that touches those APIs, and it follows three
-- rules:
--
--   1. Ask C_Secrets whether unit stats are secret *before* reading them.
--   2. Test every value with issecretvalue() before doing anything else
--      with it, and never store a secret anywhere.
--   3. When the real speed is hidden, estimate it from what is still
--      public: whether the player is moving, plus the run/swim/mounted
--      speeds remembered from the last time they were readable. The frame
--      is then flagged ESTIMATED. Walka never relays a hidden value.

local ADDON_NAME, ns = ...

local Game = {}
ns.Game = Game

local issecretvalue = issecretvalue
local function isSecret(value)
    if issecretvalue then
        return issecretvalue(value)
    end
    return false
end

-- Turn an API result into a plain boolean; secrets become `fallback`.
local function plainBool(value, fallback)
    if isSecret(value) then
        return fallback or false
    end
    return value and true or false
end

-- Last readable speeds, in yards/second.
local remembered = {
    run = 7.0,        -- on foot
    mounted = nil,    -- on a mount (unknown until seen)
    swim = 4.7222,
}
Game.remembered = remembered

local function statsAreSecret()
    local secrets = C_Secrets
    if secrets and secrets.ShouldUnitStatsBeSecret then
        local ok, hidden = pcall(secrets.ShouldUnitStatsBeSecret)
        if not ok then
            return true
        end
        return plainBool(hidden, true)
    end
    return false
end

-- Reads the real speed. Returns nil when it is hidden.
local function readRealSpeed(mounted, dead)
    if statsAreSecret() then
        return nil
    end
    local current, run, _, swim = GetUnitSpeed("player")
    if isSecret(current) or isSecret(run) or isSecret(swim) then
        return nil
    end
    if type(current) ~= "number" then
        return nil
    end
    -- (Not while dead: a ghost runs faster than the living character will
    -- when the estimate is needed.)
    if type(run) == "number" and run > 0 and not dead then
        if mounted then
            remembered.mounted = run
        else
            remembered.run = run
        end
    end
    if type(swim) == "number" and swim > 0 then
        remembered.swim = swim
    end
    return current
end

local function estimateSpeed(mounted, swimming)
    if not plainBool(IsPlayerMoving and IsPlayerMoving(), false) then
        return 0
    end
    if swimming then
        return remembered.swim
    end
    if mounted then
        return remembered.mounted or remembered.run * 1.6
    end
    return remembered.run
end

local state = {
    speed = 0,          -- yards/second, always a plain number
    estimated = false,  -- true when the real speed was hidden
    moving = false,
    mounted = false,
    taxi = false,
    swimming = false,
    flying = false,
    dead = false,
    combat = false,
    afk = false,
    falling = false,
    resting = false,
}

--- Sample the character's state. Returns a table that is reused between
-- calls; copy anything you need to keep.
function Game.Read()
    local mounted = plainBool(IsMounted and IsMounted(), false)
    local swimming = plainBool(IsSwimming and IsSwimming(), false)
    local dead = plainBool(UnitIsDeadOrGhost and UnitIsDeadOrGhost("player"), false)

    -- pcall is a last line of defence: if a client build hides a value this
    -- code did not expect, fall back to the estimate instead of erroring
    -- ten times a second.
    local ok, speed = pcall(readRealSpeed, mounted, dead)
    if ok and speed then
        state.speed = speed
        state.estimated = false
    else
        local okEstimate, estimate = pcall(estimateSpeed, mounted, swimming)
        state.speed = okEstimate and estimate or 0
        state.estimated = true
    end

    state.moving = state.speed > 0
    state.mounted = mounted
    state.swimming = swimming
    state.taxi = plainBool(UnitOnTaxi and UnitOnTaxi("player"), false)
    state.flying = plainBool(IsFlying and IsFlying(), false)
    state.dead = dead
    state.combat = plainBool(UnitAffectingCombat and UnitAffectingCombat("player"), false)
    -- UnitIsAFK is hidden inside dungeons/raids/battlegrounds.
    state.afk = plainBool(UnitIsAFK and UnitIsAFK("player"), false)
    state.falling = plainBool(IsFalling and IsFalling(), false)
    state.resting = plainBool(IsResting and IsResting(), false)
    return state
end

--- "Name-Realm" of the current character.
function Game.CharacterName()
    -- Rule 2 above: look at a value only after issecretvalue() has cleared
    -- it. Even `UnitName("player") or "?"` is a (truth) test on the result.
    local name = UnitName and UnitName("player")
    if isSecret(name) or type(name) ~= "string" or name == "" then
        name = "?"
    end
    local realm = GetRealmName and GetRealmName()
    if isSecret(realm) or type(realm) ~= "string" then
        realm = ""
    end
    if realm ~= "" then
        return name .. "-" .. realm
    end
    return name
end

--- Seconds since the epoch on this computer's clock. The companion app
-- stamps its sessions with the same clock, which is how the two are matched.
function Game.Now()
    return time()
end

return Game
