-- wowmock: a stand-in for the World of Warcraft: Forever client, enough of it
-- to load the Walka addon and drive it from pytest (see ../harness.py).
--
-- This file is test code. It runs in the host Lua state; the addon runs in a
-- separate global environment `G` that only contains what the real client
-- gives an addon. What exists is decided by the API snapshot
-- (../data/forever_api.json.gz), reached through `host`:
--
--   * a widget only has the methods its widget type has in this client;
--   * script handlers, events, templates and font objects must exist;
--   * a global the mock does not implement is created on first use if the
--     snapshot knows it (a font object, a Blizzard frame, a namespace ...),
--     otherwise reading it gives nil - as in the game - and is recorded.
--
-- Nothing here is Blizzard code: behaviour is re-implemented minimally.

local host = ...

local M = {}

local type, tostring, tonumber, select, pairs, ipairs, next, error, unpack =
    type, tostring, tonumber, select, pairs, ipairs, next, error, unpack
local setmetatable, getmetatable, rawget, rawset, newproxy = setmetatable, getmetatable, rawget, rawset, newproxy
local floor, huge = math.floor, math.huge
local sformat = string.format

local function fail(message, level)
    error("[wowmock] " .. tostring(message), (level or 1) + 1)
end

----------------------------------------------------------------------------
-- Logs (read by the tests)
----------------------------------------------------------------------------

local log = {
    undefined = {},      -- global (or "Namespace.member") read while nil -> count
    created = {},        -- globals the addon created
    declared = {},       -- names the addon may leave nil (SavedVariables)
    unimplemented = {},  -- exists in the client, but the mock has nothing for it
    lod = {},            -- defined only by a load-on-demand Blizzard addon
    menus = {},          -- context menus opened (MenuUtil.CreateContextMenu)
    noop = {},           -- "Type:Method" accepted without behaviour
    mixin_stub = {},     -- "Mixin:Method" template methods called as no-ops
    printed = {},        -- lines printed to the chat frame
    errors = {},         -- messages given to the error handler
    sounds = {},
    popups = {},
    settings_opened = {},
    blocked = {},
    reloads = {},        -- ReloadUI() calls: the clock (state.clock) at each
    played_requests = {},-- RequestTimePlayed() calls: the clock at each
    screenshots = {},    -- Screenshot() calls: the clock at each
}
M.log = log

----------------------------------------------------------------------------
-- Game state (set by the tests)
----------------------------------------------------------------------------

local state = {
    -- movement, yards/second
    speed = 0,
    runSpeed = 7,
    flightSpeed = 7,
    swimSpeed = 4.7222,
    moving = nil,            -- nil: speed > 0
    mounted = false,
    swimming = false,
    falling = false,
    flying = false,
    taxi = false,
    dead = false,
    combat = false,          -- UnitAffectingCombat("player")
    lockdown = nil,          -- InCombatLockdown(); nil: same as combat
    afk = false,
    resting = false,
    -- secrecy (Midnight "secret values")
    statsSecret = false,     -- unit stats restricted: GetUnitSpeed is secret
    chatLockdown = false,    -- chat messaging lockdown: UnitIsAFK is secret
    nameSecret = false,      -- unit name identity restricted: UnitName is secret
    identitySecret = false,  -- unit identity restricted: UnitClass is secret
    predicateErrors = false, -- C_Secrets.ShouldUnitStatsBeSecret() raises
    predicateLies = false,   -- ... or says "not secret" while values are
    -- client
    physicalWidth = 1920,
    physicalHeight = 1080,
    locale = "enUS",
    name = "Tester",
    realm = "Azeroth",
    -- the character
    level = 17,
    maxLevel = 60,           -- GetMaxPlayerLevel()
    -- where the character is (C_Map, IsInInstance)
    guid = "Player-4321-0ABCDEF1",
    instance = false,        -- inside a dungeon, raid or battleground
    continent = 1,           -- world continent id; nil: the game cannot place the character
    worldX = -600,           -- world yards, north positive
    worldY = -4200,          -- world yards, west positive
    mapId = nil,             -- GetBestMapForUnit(); nil: worked out from the position
    facing = 0,              -- GetPlayerFacing(), radians
    positionSecret = false,  -- the position calls return secrets
    mapsBroken = false,      -- the game answers nothing useful about its maps
    className = "Rogue",     -- UnitClass("player"): localised name, file name, id
    classFile = "ROGUE",
    classId = 4,
    faction = "Alliance",    -- UnitFactionGroup("player"): "Alliance", "Horde" or "Neutral"
    played = 77100,          -- /played total in seconds; grows while the game runs
    playedThisLevel = 4000,
    playedAnswers = true,    -- false: the server never answers RequestTimePlayed()
    cursorX = 0,
    cursorY = 0,
    shift = false,
    ctrl = false,
    alt = false,
    clock = 1795000000,      -- seconds since the epoch (may be fractional)
    uptime = 5000,           -- GetTime()
}
M.state = state

----------------------------------------------------------------------------
-- Secret values
----------------------------------------------------------------------------
-- The client hands tainted code "secret" numbers/strings/booleans on which
-- almost every operation raises an error. Here a secret is a userdata whose
-- metatable raises on arithmetic, ordering, concatenation, length, indexing
-- and calling.
--
-- Limits of Lua 5.1 (documented in tests/addon/__init__.py): `secret == x`
-- against a non-secret is simply false, a secret is always truthy, and it
-- can be used as a table key without complaint. The real client raises in
-- all three cases.

local secretFlavour = setmetatable({}, { __mode = "k" })

local function secretOp(what)
    return function()
        error("attempt to " .. what .. " a secret value (test with issecretvalue() first)", 2)
    end
end

local secretProto = newproxy(true)
do
    local mt = getmetatable(secretProto)
    for _, op in ipairs({ "__add", "__sub", "__mul", "__div", "__mod", "__pow", "__unm" }) do
        mt[op] = secretOp("perform arithmetic on")
    end
    mt.__concat = secretOp("concatenate")
    mt.__len = secretOp("get the length of")
    mt.__lt = secretOp("compare")
    mt.__le = secretOp("compare")
    mt.__eq = secretOp("compare")
    mt.__index = secretOp("index")
    mt.__newindex = secretOp("index")
    mt.__call = secretOp("call")
    mt.__metatable = false
end

local function makeSecret(flavour)
    local secret = newproxy(secretProto)
    secretFlavour[secret] = flavour or "number"
    return secret
end
M.make_secret = makeSecret

local function isSecret(value)
    return secretFlavour[value] ~= nil
end
M.is_secret = isSecret

--- Replace every value by a fresh secret when `restricted`.
local function maybeSecret(restricted, flavour, ...)
    if not restricted then
        return ...
    end
    local n = select("#", ...)
    local out = {}
    for i = 1, n do
        out[i] = makeSecret(flavour)
    end
    return unpack(out, 1, n)
end

----------------------------------------------------------------------------
-- The addon's global environment
----------------------------------------------------------------------------

local G = {}
M.G = G
G._G = G

local defined = {}       -- names the mock put into G itself
M.defined = defined

local function define(name, value)
    defined[name] = true
    rawset(G, name, value)
    return value
end

local kindCache = {}
local function kindOf(name)
    local kind = kindCache[name]
    if kind == nil then
        kind = host.global_kind(name) or false
        kindCache[name] = kind
    end
    return kind or nil
end

local function strictStub(fullName)
    return function()
        fail(fullName .. "() exists in this client, but the mock has no implementation of it - "
            .. "add one (or an explicit no-op) to tests/addon/lua/wow_env.lua", 2)
    end
end

local materialize   -- defined near the end: creates Blizzard globals on demand

setmetatable(G, {
    __index = function(_, key)
        if type(key) ~= "string" then
            return nil
        end
        if log.created[key] or log.declared[key] then
            return nil      -- the addon's own global, currently nil
        end
        local value = materialize(key)
        if value ~= nil then
            rawset(G, key, value)
            return value
        end
        log.undefined[key] = (log.undefined[key] or 0) + 1
        return nil
    end,
    __newindex = function(_, key, value)
        log.created[key] = true
        rawset(G, key, value)
    end,
})

----------------------------------------------------------------------------
-- Lua standard library, as the client exposes it
----------------------------------------------------------------------------
-- No os, io, package, require, dofile or debug.* - an addon that reaches for
-- them fails here as it would in the game.

for _, name in ipairs({
    "assert", "error", "ipairs", "next", "pairs", "pcall", "select", "unpack", "xpcall",
    "setmetatable", "getmetatable", "rawget", "rawset", "rawequal", "loadstring",
    "collectgarbage", "newproxy",
}) do
    define(name, _G[name])
end

define("type", function(...)
    if select("#", ...) == 0 then
        return type()       -- raises the usual "value expected"
    end
    local value = ...
    -- A real secret keeps its Lua type; only using it is forbidden.
    return secretFlavour[value] or type(value)
end)

define("tostring", function(value)
    if secretFlavour[value] then
        error("attempt to convert a secret value to a string (test with issecretvalue() first)", 2)
    end
    return tostring(value)
end)

define("tonumber", function(value, base)
    if secretFlavour[value] then
        return value        -- still secret: using the result raises
    end
    return tonumber(value, base)
end)

define("setfenv", function(target, env)
    return setfenv(type(target) == "number" and target + 1 or target, env)
end)
define("getfenv", function(target)
    if target == nil or target == 0 then
        return G
    end
    return getfenv(type(target) == "number" and target + 1 or target)
end)

-- WoW's additions to the string library are reachable as methods on any
-- string, so they go into the real string table.
function string.trim(text, chars)
    if type(text) ~= "string" then
        error("bad argument #1 to 'strtrim' (string expected)", 2)
    end
    chars = chars or " \t\r\n"
    local class = "[" .. chars:gsub("[%^%]%-%%]", "%%%0") .. "]"
    return (text:gsub("^" .. class .. "+", ""):gsub(class .. "+$", ""))
end
function string.split(delimiters, text, pieces)
    if type(delimiters) ~= "string" or type(text) ~= "string" then
        error("Usage: strsplit(delimiter, subject[, pieces])", 2)
    end
    local out, start, count = {}, 1, 0
    while true do
        count = count + 1
        local position
        if not pieces or count < pieces then
            for i = start, #text do
                if delimiters:find(text:sub(i, i), 1, true) then
                    position = i
                    break
                end
            end
        end
        if not position then
            out[count] = text:sub(start)
            break
        end
        out[count] = text:sub(start, position - 1)
        start = position + 1
    end
    return unpack(out, 1, count)
end
function string.join(delimiter, ...)
    local parts = {}
    for i = 1, select("#", ...) do
        parts[i] = tostring((select(i, ...)))
    end
    return table.concat(parts, delimiter)
end
function table.wipe(t)
    for key in pairs(t) do
        t[key] = nil
    end
    return t
end

define("string", string)
define("table", table)
define("math", math)
define("coroutine", coroutine)

local bit = {}
do
    local function toBits(value)
        value = value % 4294967296
        return floor(value)
    end
    local function bitwise(a, b, op)
        a, b = toBits(a), toBits(b)
        local result, place = 0, 1
        for _ = 1, 32 do
            local x, y = a % 2, b % 2
            if op(x, y) then
                result = result + place
            end
            a, b, place = (a - x) / 2, (b - y) / 2, place * 2
        end
        return result
    end
    function bit.band(a, b) return bitwise(a, b, function(x, y) return x == 1 and y == 1 end) end
    function bit.bor(a, b) return bitwise(a, b, function(x, y) return x == 1 or y == 1 end) end
    function bit.bxor(a, b) return bitwise(a, b, function(x, y) return x ~= y end) end
    function bit.bnot(a) return 4294967295 - toBits(a) end
    function bit.lshift(a, n) return toBits(toBits(a) * 2 ^ n) end
    function bit.rshift(a, n) return floor(toBits(a) / 2 ^ n) end
end
define("bit", bit)

-- Global aliases the client defines for library functions.
local aliases = {
    abs = math.abs, ceil = math.ceil, floor = math.floor, max = math.max, min = math.min,
    mod = math.fmod, sqrt = math.sqrt, random = math.random, exp = math.exp, log = math.log,
    sin = math.sin, cos = math.cos, tan = math.tan, deg = math.deg, rad = math.rad,
    format = string.format, gsub = string.gsub, gmatch = string.gmatch, strbyte = string.byte,
    strchar = string.char, strfind = string.find, strlen = string.len, strlower = string.lower,
    strupper = string.upper, strmatch = string.match, strrep = string.rep, strrev = string.reverse,
    strsub = string.sub, strtrim = string.trim, strsplit = string.split, strjoin = string.join,
    tinsert = table.insert, tremove = table.remove, sort = table.sort, wipe = table.wipe,
    getn = table.getn,
}
for name, fn in pairs(aliases) do
    define(name, fn)
end

define("tostringall", function(...)
    local n = select("#", ...)
    local out = {}
    for i = 1, n do
        out[i] = G.tostring((select(i, ...)))
    end
    return unpack(out, 1, n)
end)

define("tContains", function(t, item)
    for _, value in pairs(t) do
        if value == item then
            return true
        end
    end
    return false
end)

define("print", function(...)
    local parts = {}
    for i = 1, select("#", ...) do
        parts[i] = G.tostring((select(i, ...)))
    end
    log.printed[#log.printed + 1] = table.concat(parts, " ")
end)

-- date() and time() are Lua's os.date and os.time, and the game runs on
-- Windows. Its C runtime cannot convert a time before 1970 or after the
-- year 3000: localtime() fails, and Lua 5.1 then returns nil from os.date
-- (and from os.time for such a date) instead of a string. The tests run on
-- other systems, whose C library is more forgiving, so the Windows limits
-- are applied here: code that does `"..." .. date(format, when)` on a
-- timestamp it did not check must fail in the tests as it would in the game.
local WINDOWS_MAX_TIME = 32535215999     -- 3000-12-31 23:59:59 UTC
M.max_time = WINDOWS_MAX_TIME

local function convertibleTime(when)
    return when == when and when >= 0 and when <= WINDOWS_MAX_TIME
end

define("date", function(pattern, when)
    if isSecret(when) or isSecret(pattern) then
        fail("secret value passed to date()", 2)
    end
    if pattern ~= nil and type(pattern) ~= "string" then
        error("bad argument #1 to 'date' (string expected, got " .. type(pattern) .. ")", 2)
    end
    if pattern then
        -- The format goes straight to the C library's strftime. Only the
        -- conversions of ISO C89 exist everywhere; with others (%e, %T, %F,
        -- %s ...) the result differs between systems, and the Windows
        -- runtime treats an unknown one as a fatal error.
        local body = pattern:gsub("^!", "")
        if body:sub(1, 2) ~= "*t" then
            for specifier in body:gmatch("%%(.?)") do
                if not specifier:find("^[aAbBcdHIjmMpSUwWxXyYZ%%]$") then
                    fail("date(\"" .. pattern .. "\"): %" .. specifier .. " is not an ISO C89 strftime conversion; "
                        .. "it is not safe on the Windows C runtime the game uses", 2)
                end
            end
        end
    end
    if when ~= nil then
        local number = tonumber(when)
        if not number then
            error("bad argument #2 to 'date' (number expected, got " .. type(when) .. ")", 2)
        end
        if not convertibleTime(number) then
            return nil
        end
        when = floor(number)
    end
    return os.date(pattern or "%c", when or floor(state.clock))
end)
define("time", function(fields)
    if fields ~= nil then
        if type(fields) ~= "table" then
            error("bad argument #1 to 'time' (table expected, got " .. type(fields) .. ")", 2)
        end
        local ok, result = pcall(os.time, fields)
        if not ok then
            error(result, 2)        -- "field 'day' missing in date table"
        end
        if result == nil or not convertibleTime(result) then
            return nil
        end
        return result
    end
    return floor(state.clock)
end)
define("GetTime", function()
    return state.uptime
end)
define("debugstack", function(start)
    return debug.traceback("", (tonumber(start) or 1) + 1)
end)
define("debugprofilestop", function()
    return os.clock() * 1000
end)

local function errorHandler(message)
    log.errors[#log.errors + 1] = tostring(message)
end
local currentErrorHandler = errorHandler
define("geterrorhandler", function()
    return currentErrorHandler
end)
define("seterrorhandler", function(handler)
    if type(handler) ~= "function" then
        fail("seterrorhandler(): function expected", 2)
    end
    currentErrorHandler = handler
end)

define("issecure", function() return false end)
define("securecall", function(fn, ...)
    if type(fn) == "string" then
        fn = G[fn]
    end
    return fn(...)
end)
define("securecallfunction", function(fn, ...)
    return fn(...)
end)

define("hooksecurefunc", function(a, b, c)
    local tbl, name, hook
    if type(a) == "table" then
        tbl, name, hook = a, b, c
    else
        tbl, name, hook = G, a, b
    end
    local original = tbl[name]
    if type(original) ~= "function" or type(hook) ~= "function" then
        fail("hooksecurefunc(): " .. tostring(name) .. " is not a function", 2)
    end
    rawset(tbl, name, function(...)
        local results = { original(...) }
        hook(...)
        return unpack(results, 1, table.maxn(results))
    end)
end)

-- Secret-value predicates.
define("issecretvalue", isSecret)
define("canaccessvalue", function(value) return not isSecret(value) end)
define("canaccesssecrets", function() return false end)
define("hasanysecretvalues", function(...)
    for i = 1, select("#", ...) do
        if isSecret((select(i, ...))) then
            return true
        end
    end
    return false
end)
define("canaccessallvalues", function(...)
    return not G.hasanysecretvalues(...)
end)
define("issecrettable", function(t)
    if isSecret(t) then
        return true
    end
    if type(t) == "table" then
        for key, value in pairs(t) do
            if isSecret(key) or isSecret(value) then
                return true
            end
        end
    end
    return false
end)
define("canaccesstable", function(t) return not G.issecrettable(t) end)
define("scrubsecretvalues", function(...)
    local n = select("#", ...)
    local out = {}
    for i = 1, n do
        local value = select(i, ...)
        if not isSecret(value) then
            out[i] = value
        end
    end
    return unpack(out, 1, n)
end)

-- Mixins.
define("Mixin", function(object, ...)
    for i = 1, select("#", ...) do
        local mixin = select(i, ...)
        if type(mixin) ~= "table" then
            fail("Mixin(): argument " .. (i + 1) .. " is not a table", 2)
        end
        for key, value in pairs(mixin) do
            object[key] = value
        end
    end
    return object
end)
define("CreateFromMixins", function(...)
    return G.Mixin({}, ...)
end)

----------------------------------------------------------------------------
-- Game API
----------------------------------------------------------------------------

local function unitArgument(name, unit, optional)
    if unit == nil and optional then
        return "player"
    end
    if isSecret(unit) then
        fail("secret value passed to " .. name .. "()", 3)
    end
    if type(unit) ~= "string" then
        fail("Usage: " .. name .. "(unit) - got " .. type(unit), 3)
    end
    return string.lower(unit)
end

local function playerOnly(name, unit)
    if unit ~= "player" then
        fail(name .. "(\"" .. unit .. "\"): the mock only models the player", 3)
    end
end

define("GetUnitSpeed", function(unit)
    unit = unitArgument("GetUnitSpeed", unit)
    playerOnly("GetUnitSpeed", unit)
    return maybeSecret(state.statsSecret, "number", state.speed, state.runSpeed, state.flightSpeed, state.swimSpeed)
end)

define("IsPlayerMoving", function()
    if state.moving ~= nil then
        return state.moving and true or false
    end
    return state.speed > 0
end)
define("IsMounted", function() return state.mounted and true or false end)
define("IsResting", function() return state.resting and true or false end)
define("IsSwimming", function(unit)
    playerOnly("IsSwimming", unitArgument("IsSwimming", unit, true))
    return state.swimming and true or false
end)
define("IsFalling", function(unit)
    playerOnly("IsFalling", unitArgument("IsFalling", unit, true))
    return state.falling and true or false
end)
define("IsFlying", function(unit)
    playerOnly("IsFlying", unitArgument("IsFlying", unit, true))
    return state.flying and true or false
end)
define("UnitOnTaxi", function(unit)
    playerOnly("UnitOnTaxi", unitArgument("UnitOnTaxi", unit))
    return state.taxi and true or false
end)
define("UnitIsDeadOrGhost", function(unit)
    playerOnly("UnitIsDeadOrGhost", unitArgument("UnitIsDeadOrGhost", unit))
    return state.dead and true or false
end)
define("UnitAffectingCombat", function(unit)
    playerOnly("UnitAffectingCombat", unitArgument("UnitAffectingCombat", unit))
    return state.combat and true or false
end)
define("UnitIsAFK", function(unit)
    playerOnly("UnitIsAFK", unitArgument("UnitIsAFK", unit))
    return maybeSecret(state.chatLockdown, "boolean", state.afk and true or false)
end)
define("UnitName", function(unit)
    playerOnly("UnitName", unitArgument("UnitName", unit))
    if state.nameSecret then
        return makeSecret("string"), nil
    end
    return state.name, nil
end)
define("UnitExists", function(unit)
    return unitArgument("UnitExists", unit) == "player"
end)
define("UnitLevel", function(unit)
    playerOnly("UnitLevel", unitArgument("UnitLevel", unit))
    return state.level
end)
define("GetMaxPlayerLevel", function() return state.maxLevel end)
define("UnitClass", function(unit)
    playerOnly("UnitClass", unitArgument("UnitClass", unit))
    if state.identitySecret then
        return makeSecret("string"), makeSecret("string"), makeSecret("number")
    end
    return state.className, state.classFile, state.classId
end)
define("UnitFactionGroup", function(unit)
    playerOnly("UnitFactionGroup", unitArgument("UnitFactionGroup", unit))
    return state.faction, state.faction
end)
define("GetRealmName", function() return state.realm end)
define("GetNormalizedRealmName", function() return (string.gsub(state.realm, "[%s%-]", "")) end)
define("GetLocale", function() return state.locale end)
define("InCombatLockdown", function()
    if state.lockdown ~= nil then
        return state.lockdown and true or false
    end
    return state.combat and true or false
end)
define("GetPhysicalScreenSize", function()
    return state.physicalWidth, state.physicalHeight
end)
define("GetBuildInfo", function()
    return "1.60.1", "70959", "Nov 10 2026", 16001, "", " ", 16001
end)
define("GetFramerate", function() return 60 end)
define("GetCursorPosition", function() return state.cursorX, state.cursorY end)
define("IsShiftKeyDown", function() return state.shift and true or false end)
define("IsControlKeyDown", function() return state.ctrl and true or false end)
define("IsAltKeyDown", function() return state.alt and true or false end)
define("IsModifierKeyDown", function() return (state.shift or state.ctrl or state.alt) and true or false end)
define("IsMouseButtonDown", function() return false end)
define("PlaySound", function(soundKitID, channel)
    if type(soundKitID) ~= "number" then
        fail("PlaySound(): soundKitID must be a number, got " .. type(soundKitID), 2)
    end
    log.sounds[#log.sounds + 1] = soundKitID
    return true, #log.sounds
end)

-- RequestTimePlayed() asks the server; the answer comes back a moment later
-- as TIME_PLAYED_MSG (see M.run_frame). Nothing else gives an addon the
-- time played.
local playedAnswersDue = 0
define("RequestTimePlayed", function()
    log.played_requests[#log.played_requests + 1] = state.clock
    if state.playedAnswers ~= false then
        playedAnswersDue = playedAnswersDue + 1
    end
end)

-- Screenshot() saves a picture of the whole screen to the Screenshots
-- folder. The mock only records that it was asked to.
define("Screenshot", function()
    log.screenshots[#log.screenshots + 1] = state.clock
end)

-- C_UI.Reload() throws the whole Lua state away and loads the interface
-- again. The mock only records that it was asked to: the state stays alive,
-- so a test can count the calls and look at what the addon did before them.
-- A test that wants what a reload does (saved variables through the
-- serialiser into a fresh state) calls WowRuntime.reload() in harness.py.
local C_UI = {}
function C_UI.Reload()
    log.reloads[#log.reloads + 1] = state.clock
end

-- ReloadUI() is Blizzard Lua (Blizzard_SharedXML/InterfaceUtil.lua) and
-- does just this.
define("ReloadUI", function()
    G.C_UI.Reload()
end)

-- C_Secrets: predicates that say whether a class of API returns secrets.
local C_Secrets = {}
function C_Secrets.ShouldUnitStatsBeSecret()
    if state.predicateErrors then
        error("C_Secrets.ShouldUnitStatsBeSecret(): simulated failure", 2)
    end
    if state.predicateLies then
        return false
    end
    return state.statsSecret and true or false
end

-- C_Timer
local timers = {}
local timerSerial = 0
local function addTimer(delay, callback, iterations)
    if type(delay) ~= "number" or delay ~= delay or delay < 0 then
        fail("C_Timer: duration must be a non-negative number", 3)
    end
    if type(callback) ~= "function" then
        fail("C_Timer: callback must be a function", 3)
    end
    timerSerial = timerSerial + 1
    local timer = { due = state.uptime + delay, delay = delay, callback = callback,
        left = iterations, serial = timerSerial, cancelled = false }
    timers[#timers + 1] = timer
    function timer:Cancel() self.cancelled = true end
    function timer:IsCancelled() return self.cancelled end
    return timer
end
local C_Timer = {}
function C_Timer.After(delay, callback)
    addTimer(delay, callback, 1)
end
function C_Timer.NewTimer(delay, callback)
    return addTimer(delay, callback, 1)
end
function C_Timer.NewTicker(delay, callback, iterations)
    return addTimer(delay, callback, iterations or huge)
end

local function runTimers()
    -- Run everything that is due, oldest first; callbacks may add timers.
    local guard = 0
    while true do
        local best
        for index = 1, #timers do
            local timer = timers[index]
            if not timer.cancelled and timer.due <= state.uptime
                and (not best or timer.due < best.due or (timer.due == best.due and timer.serial < best.serial)) then
                best = timer
            end
        end
        if not best then
            break
        end
        best.left = best.left - 1
        if best.left <= 0 then
            best.cancelled = true
        else
            best.due = best.due + math.max(best.delay, 0.001)
        end
        best.callback(best)
        guard = guard + 1
        if guard > 100000 then
            fail("C_Timer: runaway timers")
        end
    end
    for index = #timers, 1, -1 do
        if timers[index].cancelled then
            table.remove(timers, index)
        end
    end
end

-- C_AddOns: answers from the TOC of the addon under test.
local addon = { name = "?", toc = {} }
M.addon = addon
local C_AddOns = {}
function C_AddOns.GetAddOnMetadata(name, field)
    if type(name) ~= "string" or type(field) ~= "string" then
        fail("Usage: C_AddOns.GetAddOnMetadata(name, variable)", 2)
    end
    if name ~= addon.name then
        return nil
    end
    return addon.toc[field]
end
function C_AddOns.IsAddOnLoaded(name)
    local loaded = name == addon.name
    return loaded, loaded
end
function C_AddOns.DoesAddOnExist(name)
    return name == addon.name
end

-- C_Map: a small world. The world map (947) has two continents; each
-- continent map covers a rectangle of world yards (x grows to the north, y
-- to the west, as in the game), and one zone lies inside Kalimdor. Map art
-- comes in 256-pixel tiles, as file ids.
local MAPS = {
    [947] = { name = "Azeroth", type = "World" },
    [1414] = { name = "Kalimdor", type = "Continent", parent = 947, continent = 1,
               top = 12000, bottom = -12000, left = 4000, right = -12000 },
    [1415] = { name = "Eastern Kingdoms", type = "Continent", parent = 947, continent = 0,
               top = 7000, bottom = -17000, left = 6000, right = -10000 },
    [1411] = { name = "Durotar", type = "Zone", parent = 1414, continent = 1,
               top = 2000, bottom = -2000, left = -3000, right = -6000 },
}
M.maps = MAPS

local function mapArgument(fn, mapId)
    if type(mapId) ~= "number" then
        fail(fn .. "(): uiMapID must be a number, got " .. type(mapId), 3)
    end
    return MAPS[mapId]
end

local function mapTypeValue(name)
    return G.Enum.UIMapType[name]
end

local function vector(x, y)
    return { x = x, y = y, GetXY = function(self) return self.x, self.y end }
end
define("CreateVector2D", function(x, y)
    if type(x) ~= "number" or type(y) ~= "number" then
        fail("CreateVector2D(): two numbers expected", 2)
    end
    return vector(x, y)
end)

local function mapInfo(mapId)
    local map = MAPS[mapId]
    return { mapID = mapId, name = map.name, mapType = mapTypeValue(map.type), parentMapID = map.parent or 0 }
end

local C_Map = {}
function C_Map.GetBestMapForUnit(unit)
    playerOnly("C_Map.GetBestMapForUnit", unitArgument("C_Map.GetBestMapForUnit", unit))
    if state.positionSecret then
        return makeSecret("number")
    end
    if state.mapId ~= nil then
        return state.mapId
    end
    if state.instance or state.continent == nil then
        return nil
    end
    local zone = MAPS[1411]
    if state.continent == zone.continent and state.worldX <= zone.top and state.worldX >= zone.bottom
        and state.worldY <= zone.left and state.worldY >= zone.right then
        return 1411
    end
    for mapId, map in pairs(MAPS) do
        if map.type == "Continent" and map.continent == state.continent then
            return mapId
        end
    end
    return nil
end
function C_Map.GetPlayerMapPosition(mapId, unit)
    local map = mapArgument("C_Map.GetPlayerMapPosition", mapId)
    playerOnly("C_Map.GetPlayerMapPosition", unitArgument("C_Map.GetPlayerMapPosition", unit))
    if state.positionSecret then
        return makeSecret("table")
    end
    if not map or not map.continent or state.instance or state.continent ~= map.continent then
        return nil
    end
    local u = (state.worldY - map.left) / (map.right - map.left)
    local v = (state.worldX - map.top) / (map.bottom - map.top)
    if u < 0 or u > 1 or v < 0 or v > 1 then
        return nil
    end
    return vector(u, v)
end
function C_Map.GetWorldPosFromMapPos(mapId, position)
    local map = mapArgument("C_Map.GetWorldPosFromMapPos", mapId)
    if state.mapsBroken then
        return nil
    end
    if type(position) ~= "table" or type(position.x) ~= "number" or type(position.y) ~= "number" then
        fail("C_Map.GetWorldPosFromMapPos(): a Vector2D expected", 2)
    end
    if not map or not map.continent then
        return nil
    end
    return map.continent, vector(
        map.top + position.y * (map.bottom - map.top),
        map.left + position.x * (map.right - map.left)
    )
end
function C_Map.GetMapInfo(mapId)
    return mapArgument("C_Map.GetMapInfo", mapId) and mapInfo(mapId) or nil
end
function C_Map.GetMapChildrenInfo(mapId, mapType, allDescendants)
    mapArgument("C_Map.GetMapChildrenInfo", mapId)
    local children = {}
    if state.mapsBroken then
        return children
    end
    for childId, map in pairs(MAPS) do
        if map.parent == mapId and (mapType == nil or mapTypeValue(map.type) == mapType) then
            children[#children + 1] = mapInfo(childId)
        end
    end
    table.sort(children, function(a, b) return a.mapID < b.mapID end)
    return children
end
function C_Map.GetMapArtLayers(mapId)
    if not mapArgument("C_Map.GetMapArtLayers", mapId) then
        return {}
    end
    return { { layerWidth = 1002, layerHeight = 668, tileWidth = 256, tileHeight = 256,
               minScale = 0.4, maxScale = 1.5, additionalZoomSteps = 2 } }
end
function C_Map.GetMapArtLayerTextures(mapId, layerIndex)
    if not mapArgument("C_Map.GetMapArtLayerTextures", mapId) or layerIndex ~= 1 then
        return {}
    end
    local textures = {}
    for index = 1, 12 do     -- four columns by three rows
        textures[index] = mapId * 100 + index
    end
    return textures
end

define("IsInInstance", function()
    if state.instance then
        return true, "party"
    end
    return false, "none"
end)
define("UnitGUID", function(unit)
    playerOnly("UnitGUID", unitArgument("UnitGUID", unit))
    if state.identitySecret then
        return makeSecret("string")
    end
    return state.guid
end)
define("GetPlayerFacing", function() return state.facing end)

local namespaceImpl = {
    C_Secrets = C_Secrets,
    C_Timer = C_Timer,
    C_AddOns = C_AddOns,
    C_UI = C_UI,
    C_Map = C_Map,
}
M.namespace_impl = namespaceImpl

local restrictedCache = {}
local function makeNamespace(name)
    local impl = namespaceImpl[name] or {}
    local functions = host.namespace(name) or {}
    local namespace = {}
    for key, fn in pairs(impl) do
        namespace[key] = fn
    end
    return setmetatable(namespace, {
        __index = function(t, key)
            if type(key) ~= "string" then
                return nil
            end
            local full = name .. "." .. key
            if functions[key] then
                log.unimplemented[full] = true
                local stub = strictStub(full)
                rawset(t, key, stub)
                return stub
            end
            log.undefined[full] = (log.undefined[full] or 0) + 1
            return nil
        end,
    })
end

----------------------------------------------------------------------------
-- Widgets
----------------------------------------------------------------------------
-- A widget is, as in the client, a table whose [0] is a userdata and whose
-- metatable supplies the methods. The mock keeps its own state in `W`, so
-- the widget table holds nothing but what the addon puts there.

local W = setmetatable({}, { __mode = "k" })
local allWidgets = {}
M.widgets = allWidgets

local classes = {}
local function class(name)
    local c = classes[name]
    if not c then
        c = {}
        classes[name] = c
    end
    return c
end
M.classes = classes

local typeInfo = {}
local function infoOf(widgetType)
    local info = typeInfo[widgetType]
    if info == nil then
        local chain = host.type_chain(widgetType)
        if not chain then
            typeInfo[widgetType] = false
            return nil
        end
        info = {
            chain = chain,
            methods = host.type_methods(widgetType),
            scripts = host.type_scripts(widgetType),
            isA = {},
        }
        for index = 1, #chain do
            info.isA[chain[index]] = true
        end
        typeInfo[widgetType] = info
    end
    return info or nil
end

local function isWidget(value)
    return type(value) == "table" and W[value] ~= nil
end
M.is_widget = isWidget

local function isA(widget, widgetType)
    local s = W[widget]
    return s ~= nil and infoOf(s.type).isA[widgetType] == true
end

local function describe(widget)
    local s = W[widget]
    if not s then
        return tostring(widget)
    end
    return s.type .. (s.name and (" \"" .. s.name .. "\"") or "")
end

local function guard(methodName, implementation)
    return function(self, ...)
        local s = W[self]
        if s == nil then
            fail(methodName .. "(): called on something that is not a widget "
                .. "(a '.' where a ':' was meant?)", 2)
        end
        for i = 1, select("#", ...) do
            if secretFlavour[(select(i, ...))] then
                fail("secret value passed to " .. s.type .. ":" .. methodName .. "()", 2)
            end
        end
        return implementation(self, ...)
    end
end

local function resolveMethod(widgetType, name)
    local info = infoOf(widgetType)
    if not info.methods[name] then
        return nil      -- this widget type has no such method in this client
    end
    local chain = info.chain
    for index = 1, #chain do
        local c = classes[chain[index]]
        local implementation = c and c[name]
        if implementation then
            return guard(name, implementation)
        end
    end
    local key = widgetType .. ":" .. name
    return guard(name, function()
        log.noop[key] = (log.noop[key] or 0) + 1
    end)
end

local methodTables = {}
local function methodTable(widgetType)
    local methods = methodTables[widgetType]
    if not methods then
        methods = setmetatable({}, {
            __index = function(t, key)
                if type(key) ~= "string" then
                    return nil
                end
                local method = resolveMethod(widgetType, key)
                if method then
                    rawset(t, key, method)
                end
                return method
            end,
        })
        methodTables[widgetType] = methods
    end
    return methods
end

local widgetMetas = {}
local function newWidget(widgetType, name, parent)
    if not infoOf(widgetType) then
        fail("unknown widget type " .. tostring(widgetType), 2)
    end
    local meta = widgetMetas[widgetType]
    if not meta then
        meta = { __index = methodTable(widgetType) }
        widgetMetas[widgetType] = meta
    end
    local widget = setmetatable({ [0] = newproxy(false) }, meta)
    W[widget] = {
        type = widgetType,
        objectType = widgetType,
        name = name,
        parent = parent,
        shown = true,
        alpha = 1,
        scale = 1,
        points = {},
        scripts = {},
        children = {},
        regions = {},
        serial = #allWidgets + 1,
    }
    allWidgets[#allWidgets + 1] = widget
    if parent then
        local ps = W[parent]
        local isA = infoOf(widgetType).isA
        if ps and isA.Frame then
            ps.children[#ps.children + 1] = widget
        elseif ps and isA.Region then
            ps.regions[#ps.regions + 1] = widget
        end
    end
    return widget
end

local function numberArgument(method, value, what)
    if type(value) ~= "number" or value ~= value or value == huge or value == -huge then
        fail(method .. "(): " .. what .. " must be a finite number, got " .. tostring(value), 3)
    end
    return value
end

-- ---- scripts --------------------------------------------------------------

local function runScript(widget, scriptName, ...)
    local s = W[widget]
    local handler = s and s.scripts[scriptName]
    if handler then
        handler(widget, ...)
        return true
    end
    return false
end

local function checkScript(self, method, scriptName)
    if type(scriptName) ~= "string" then
        fail(method .. "(): script name must be a string", 3)
    end
    local s = W[self]
    if not infoOf(s.type).scripts[scriptName] then
        fail(describe(self) .. ":" .. method .. "(): this widget type has no \"" .. scriptName .. "\" script", 3)
    end
end

local ScriptRegion = class("ScriptRegion")

function ScriptRegion:SetScript(scriptName, handler)
    checkScript(self, "SetScript", scriptName)
    if handler ~= nil and type(handler) ~= "function" then
        fail("SetScript(\"" .. scriptName .. "\"): handler must be a function or nil", 2)
    end
    W[self].scripts[scriptName] = handler
end

function ScriptRegion:GetScript(scriptName)
    checkScript(self, "GetScript", scriptName)
    return W[self].scripts[scriptName]
end

function ScriptRegion:HasScript(scriptName)
    return infoOf(W[self].type).scripts[scriptName] == true
end

function ScriptRegion:HookScript(scriptName, handler)
    checkScript(self, "HookScript", scriptName)
    if type(handler) ~= "function" then
        fail("HookScript(\"" .. scriptName .. "\"): handler must be a function", 2)
    end
    local scripts = W[self].scripts
    local previous = scripts[scriptName]
    if previous then
        scripts[scriptName] = function(...)
            previous(...)
            handler(...)
        end
    else
        scripts[scriptName] = handler
    end
end

-- ---- visibility -----------------------------------------------------------

local function isVisible(widget)
    local s = W[widget]
    while s do
        if not s.shown then
            return false
        end
        if not s.parent then
            return true
        end
        s = W[s.parent]
    end
    return true
end

local function shownSubtree(widget, out)
    -- `widget` and every descendant that is shown given that `widget` is.
    out[#out + 1] = widget
    local s = W[widget]
    for _, list in ipairs({ s.regions, s.children }) do
        for index = 1, #list do
            local child = list[index]
            if W[child].shown then
                shownSubtree(child, out)
            end
        end
    end
    return out
end

local function setShown(widget, shown)
    local s = W[widget]
    shown = shown and true or false
    if s.shown == shown then
        return
    end
    local parentVisible = (not s.parent) or isVisible(s.parent)
    if shown then
        s.shown = true
        if parentVisible then
            for _, each in ipairs(shownSubtree(widget, {})) do
                runScript(each, "OnShow")
            end
        end
    else
        local affected = parentVisible and shownSubtree(widget, {}) or {}
        s.shown = false
        if s.moving then
            s.moving = nil
        end
        for _, each in ipairs(affected) do
            runScript(each, "OnHide")
        end
    end
end

function ScriptRegion:Show() setShown(self, true) end
function ScriptRegion:Hide() setShown(self, false) end
function ScriptRegion:SetShown(shown) setShown(self, shown) end
function ScriptRegion:IsShown() return W[self].shown end
function ScriptRegion:IsVisible() return isVisible(self) end

function ScriptRegion:SetParent(parent)
    if type(parent) == "string" then
        parent = G[parent]
    end
    if parent ~= nil and not isWidget(parent) then
        fail("SetParent(): parent must be a frame or nil", 2)
    end
    local s = W[self]
    if s.parent == parent then
        return
    end
    local wasVisible = isVisible(self)
    local affected = wasVisible and shownSubtree(self, {}) or nil
    local listName = infoOf(s.type).isA.Frame and "children" or "regions"
    if s.parent and W[s.parent] then
        local list = W[s.parent][listName]
        for index = #list, 1, -1 do
            if list[index] == self then
                table.remove(list, index)
            end
        end
    end
    s.parent = parent
    if parent then
        local list = W[parent][listName]
        list[#list + 1] = self
    end
    local nowVisible = isVisible(self)
    if wasVisible and not nowVisible then
        for _, each in ipairs(affected) do
            runScript(each, "OnHide")
        end
    elseif nowVisible and not wasVisible then
        for _, each in ipairs(shownSubtree(self, {})) do
            runScript(each, "OnShow")
        end
    end
end

-- ---- mouse ----------------------------------------------------------------

function ScriptRegion:EnableMouse(enable) W[self].mouse = enable and true or false end
function ScriptRegion:IsMouseEnabled() return W[self].mouse == true end
function ScriptRegion:EnableMouseWheel(enable) W[self].mouseWheel = enable and true or false end
function ScriptRegion:IsMouseWheelEnabled() return W[self].mouseWheel == true end
function ScriptRegion:SetMouseClickEnabled(enable) W[self].mouse = enable and true or false end
function ScriptRegion:SetMouseMotionEnabled(enable) W[self].mouseMotion = enable and true or false end
function ScriptRegion:IsMouseClickEnabled() return W[self].mouse == true end
function ScriptRegion:IsMouseMotionEnabled() return W[self].mouseMotion == true or W[self].mouse == true end
function ScriptRegion:IsMouseOver() return W[self].mouseOver == true end
function ScriptRegion:IsMouseMotionFocus() return W[self].mouseOver == true end
function ScriptRegion:IsDragging() return W[self].moving == true end
function ScriptRegion:IsProtected() return false, false end
function ScriptRegion:CanChangeProtectedState() return true end
function ScriptRegion:IsAnchoringRestricted() return false end
function ScriptRegion:IsAnchoringSecret() return false end

-- ---- geometry -------------------------------------------------------------
-- Positions are resolved in "screen units": the screen is 768 units high
-- whatever its resolution, and a region's own units are screen units
-- divided by its effective scale.

local POINTS = {
    TOPLEFT = { 0, 1 }, TOP = { 0.5, 1 }, TOPRIGHT = { 1, 1 },
    LEFT = { 0, 0.5 }, CENTER = { 0.5, 0.5 }, RIGHT = { 1, 0.5 },
    BOTTOMLEFT = { 0, 0 }, BOTTOM = { 0.5, 0 }, BOTTOMRIGHT = { 1, 0 },
}

local function screenSize()
    return 768 * state.physicalWidth / state.physicalHeight, 768
end
M.screen_size = screenSize

local function effectiveScale(widget)
    local s = W[widget]
    local scale = s.scale
    if s.parent and not s.ignoreParentScale then
        return scale * effectiveScale(s.parent)
    end
    return scale
end

local function effectiveAlpha(widget)
    local s = W[widget]
    if s.parent and not s.ignoreParentAlpha then
        return s.alpha * effectiveAlpha(s.parent)
    end
    return s.alpha
end

local naturalSize   -- FontString's text size, defined with FontString

--- left, bottom, width, height in screen units, or nil if not resolvable.
local function rectOf(widget, visiting)
    local s = W[widget]
    local parentState = s.parent and W[s.parent]
    if parentState and parentState.scrollChild == widget then
        -- A scroll child is placed by its scroll frame, whatever anchors it
        -- has: top-left corners together, moved by the scroll offsets.
        local parentLeft, parentBottom, _, parentHeight = rectOf(s.parent, visiting)
        if not parentLeft then
            return nil
        end
        local scale = effectiveScale(widget)
        local width, height = (s.width or 0) * scale, (s.height or 0) * scale
        local left = parentLeft - (parentState.horizontalScroll or 0) * scale
        local top = parentBottom + parentHeight + (parentState.verticalScroll or 0) * scale
        return left, top - height, width, height
    end
    local points = s.points
    if #points == 0 then
        return nil
    end
    visiting = visiting or {}
    if visiting[widget] then
        return nil
    end
    visiting[widget] = true
    local scale = effectiveScale(widget)
    local left, right, centerX, bottom, top, centerY
    for index = 1, #points do
        local anchor = points[index]
        local rl, rb, rw, rh
        if anchor.relativeTo then
            rl, rb, rw, rh = rectOf(anchor.relativeTo, visiting)
            if not rl then
                visiting[widget] = nil
                return nil
            end
        else
            rl, rb = 0, 0
            rw, rh = screenSize()
        end
        local relative = POINTS[anchor.relativePoint]
        local x = rl + rw * relative[1] + anchor.x * scale
        local y = rb + rh * relative[2] + anchor.y * scale
        local own = POINTS[anchor.point]
        if own[1] == 0 then left = x elseif own[1] == 1 then right = x else centerX = x end
        if own[2] == 0 then bottom = y elseif own[2] == 1 then top = y else centerY = y end
    end
    visiting[widget] = nil

    local width = s.width and s.width > 0 and s.width * scale or nil
    local height = s.height and s.height > 0 and s.height * scale or nil
    if (not width or not height) and naturalSize then
        local nw, nh = naturalSize(widget)
        if nw then
            width = width or nw * scale
            height = height or nh * scale
        end
    end

    if left and right then
        width = right - left
    else
        width = width or 0
        if not left then
            if right then
                left = right - width
            elseif centerX then
                left = centerX - width / 2
            else
                return nil
            end
        end
    end
    if bottom and top then
        height = top - bottom
    else
        height = height or 0
        if not bottom then
            if top then
                bottom = top - height
            elseif centerY then
                bottom = centerY - height / 2
            else
                return nil
            end
        end
    end
    if s.clamped then
        -- SetClampedToScreen: wherever its anchors put it, the client keeps
        -- the frame's rectangle inside the screen.
        local screenWidth, screenHeight = screenSize()
        if width <= screenWidth then
            left = math.max(0, math.min(screenWidth - width, left))
        end
        if height <= screenHeight then
            bottom = math.max(0, math.min(screenHeight - height, bottom))
        end
    end
    return left, bottom, width, height
end
M.rect_of = rectOf

local function dependsOn(widget, target, seen)
    -- Does `widget`'s position depend on `target`?
    if widget == target then
        return true
    end
    seen = seen or {}
    if seen[widget] then
        return false
    end
    seen[widget] = true
    local points = W[widget].points
    for index = 1, #points do
        local relativeTo = points[index].relativeTo
        if relativeTo and dependsOn(relativeTo, target, seen) then
            return true
        end
    end
    return false
end

local function resolveRegion(method, value)
    if type(value) == "string" then
        local found = G[value]
        if not isWidget(found) then
            fail(method .. "(): no region named \"" .. value .. "\"", 3)
        end
        return found
    end
    if value ~= nil and not isWidget(value) then
        fail(method .. "(): relativeTo must be a region, a region name or nil", 3)
    end
    return value
end

local function setAnchor(self, anchor)
    local s = W[self]
    if anchor.relativeTo == self then
        fail(describe(self) .. ":SetPoint(): cannot anchor a region to itself", 3)
    end
    if anchor.relativeTo and dependsOn(anchor.relativeTo, self) then
        fail(describe(self) .. ":SetPoint(): cannot anchor to a region that depends on it", 3)
    end
    for index = 1, #s.points do
        if s.points[index].point == anchor.point then
            s.points[index] = anchor
            return
        end
    end
    s.points[#s.points + 1] = anchor
end

function ScriptRegion:SetPoint(point, ...)
    if type(point) ~= "string" or not POINTS[point] then
        fail(describe(self) .. ":SetPoint(): unknown anchor point " .. tostring(point), 2)
    end
    local n = select("#", ...)
    local a, b, c, d = ...
    local relativeTo, relativePoint, x, y
    local s = W[self]
    if n == 0 then
        relativeTo = s.parent
    elseif type(a) == "number" then
        relativeTo, x, y = s.parent, a, b
    else
        relativeTo = resolveRegion("SetPoint", a)
        if relativeTo == nil then
            relativeTo = s.parent
        end
        if type(b) == "string" then
            relativePoint, x, y = b, c, d
        else
            x, y = b, c
        end
    end
    relativePoint = relativePoint or point
    if not POINTS[relativePoint] then
        fail(describe(self) .. ":SetPoint(): unknown relative point " .. tostring(relativePoint), 2)
    end
    x = numberArgument("SetPoint", x or 0, "x offset")
    y = numberArgument("SetPoint", y or 0, "y offset")
    setAnchor(self, { point = point, relativeTo = relativeTo, relativePoint = relativePoint, x = x, y = y })
end

function ScriptRegion:SetAllPoints(relativeTo)
    relativeTo = resolveRegion("SetAllPoints", relativeTo)
    local s = W[self]
    if relativeTo == nil then
        relativeTo = s.parent
    end
    s.points = {}
    setAnchor(self, { point = "TOPLEFT", relativeTo = relativeTo, relativePoint = "TOPLEFT", x = 0, y = 0 })
    setAnchor(self, { point = "BOTTOMRIGHT", relativeTo = relativeTo, relativePoint = "BOTTOMRIGHT", x = 0, y = 0 })
end

function ScriptRegion:ClearAllPoints()
    W[self].points = {}
end

function ScriptRegion:ClearPoint(point)
    local points = W[self].points
    for index = #points, 1, -1 do
        if points[index].point == point then
            table.remove(points, index)
        end
    end
end

function ScriptRegion:GetNumPoints()
    return #W[self].points
end

function ScriptRegion:GetPoint(index)
    local anchor = W[self].points[index or 1]
    if not anchor then
        return nil
    end
    return anchor.point, anchor.relativeTo, anchor.relativePoint, anchor.x, anchor.y
end

function ScriptRegion:GetPointByName(point)
    local points = W[self].points
    for index = 1, #points do
        local anchor = points[index]
        if anchor.point == point then
            return anchor.point, anchor.relativeTo, anchor.relativePoint, anchor.x, anchor.y
        end
    end
    return nil
end

local function sizeChanged(self)
    if infoOf(W[self].type).isA.Frame then
        local width, height = self:GetSize()
        runScript(self, "OnSizeChanged", width, height)
    end
end

function ScriptRegion:SetSize(width, height)
    local s = W[self]
    s.width = numberArgument("SetSize", width, "width")
    s.height = numberArgument("SetSize", height, "height")
    sizeChanged(self)
end
function ScriptRegion:SetWidth(width)
    W[self].width = numberArgument("SetWidth", width, "width")
    sizeChanged(self)
end
function ScriptRegion:SetHeight(height)
    W[self].height = numberArgument("SetHeight", height, "height")
    sizeChanged(self)
end

local function sizeOf(self)
    local s = W[self]
    local width = s.width and s.width > 0 and s.width or nil
    local height = s.height and s.height > 0 and s.height or nil
    if not (width and height) then
        local _, _, rw, rh = rectOf(self)
        local scale = effectiveScale(self)
        if rw then
            width = width or rw / scale
            height = height or rh / scale
        elseif naturalSize then
            local nw, nh = naturalSize(self)
            width = width or nw
            height = height or nh
        end
    end
    return width or 0, height or 0
end

function ScriptRegion:GetSize() return sizeOf(self) end
function ScriptRegion:GetWidth() return (sizeOf(self)) end
function ScriptRegion:GetHeight() return (select(2, sizeOf(self))) end

local function ownRect(self)
    local left, bottom, width, height = rectOf(self)
    if not left then
        return nil
    end
    local scale = effectiveScale(self)
    return left / scale, bottom / scale, width / scale, height / scale
end

function ScriptRegion:GetRect() return ownRect(self) end
function ScriptRegion:GetScaledRect() return rectOf(self) end
function ScriptRegion:IsRectValid() return rectOf(self) ~= nil end
function ScriptRegion:GetLeft() return (ownRect(self)) end
function ScriptRegion:GetBottom() return (select(2, ownRect(self))) end
function ScriptRegion:GetRight()
    local left, _, width = ownRect(self)
    return left and left + width
end
function ScriptRegion:GetTop()
    local _, bottom, _, height = ownRect(self)
    return bottom and bottom + height
end
function ScriptRegion:GetCenter()
    local left, bottom, width, height = ownRect(self)
    if not left then
        return nil
    end
    return left + width / 2, bottom + height / 2
end

-- ---- FrameScriptObject / Object -------------------------------------------

local FrameScriptObject = class("FrameScriptObject")
function FrameScriptObject:GetName() return W[self].name end
function FrameScriptObject:GetObjectType() return W[self].objectType end
function FrameScriptObject:IsObjectType(widgetType)
    return infoOf(W[self].type).isA[widgetType] == true or W[self].objectType == widgetType
end
function FrameScriptObject:IsForbidden() return false end
function FrameScriptObject:HasSecretValues() return false end
function FrameScriptObject:HasAnySecretAspect() return false end

local Object = class("Object")
function Object:GetParent() return W[self].parent end
function Object:GetDebugName() return W[self].name or tostring(self) end
function Object:GetParentKey() return W[self].parentKey end
function Object:SetParentKey(key)
    local s = W[self]
    s.parentKey = key
    if s.parent then
        s.parent[key] = self
    end
end

-- ---- Region (textures, font strings) ---------------------------------------

local DRAW_LAYERS = { BACKGROUND = 1, BORDER = 2, ARTWORK = 3, OVERLAY = 4, HIGHLIGHT = 5 }
M.draw_layers = DRAW_LAYERS

local function checkLayer(method, layer, subLevel)
    if layer ~= nil and not DRAW_LAYERS[layer] then
        fail(method .. "(): unknown draw layer " .. tostring(layer), 3)
    end
    if subLevel ~= nil and (type(subLevel) ~= "number" or subLevel < -8 or subLevel > 7) then
        fail(method .. "(): sublevel must be -8..7, got " .. tostring(subLevel), 3)
    end
end

local function colorArguments(method, r, g, b, a)
    if type(r) == "table" and not isWidget(r) and r.r then
        r, g, b, a = r.r, r.g, r.b, r.a
    end
    numberArgument(method, r, "red")
    numberArgument(method, g, "green")
    numberArgument(method, b, "blue")
    if a ~= nil then
        numberArgument(method, a, "alpha")
    end
    return r, g, b, a or 1
end

local function setAlpha(self, alpha)
    W[self].alpha = numberArgument("SetAlpha", alpha, "alpha")
end
local function setScale(self, scale)
    numberArgument("SetScale", scale, "scale")
    if scale <= 0 then
        fail(describe(self) .. ":SetScale(): scale must be greater than 0, got " .. tostring(scale), 2)
    end
    W[self].scale = scale
end

local Region = class("Region")
Region.SetAlpha = setAlpha
function Region:GetAlpha() return W[self].alpha end
Region.SetScale = setScale
function Region:GetScale() return W[self].scale end
function Region:GetEffectiveScale() return effectiveScale(self) end
function Region:SetIgnoreParentScale(ignore) W[self].ignoreParentScale = ignore and true or false end
function Region:IsIgnoringParentScale() return W[self].ignoreParentScale == true end
function Region:SetIgnoreParentAlpha(ignore) W[self].ignoreParentAlpha = ignore and true or false end
function Region:IsIgnoringParentAlpha() return W[self].ignoreParentAlpha == true end
function Region:SetDrawLayer(layer, subLevel)
    checkLayer("SetDrawLayer", layer, subLevel)
    W[self].layer, W[self].subLevel = layer, subLevel or 0
end
function Region:GetDrawLayer() return W[self].layer, W[self].subLevel end
function Region:SetVertexColor(r, g, b, a)
    W[self].vertexColor = { colorArguments("SetVertexColor", r, g, b, a) }
end
function Region:GetVertexColor()
    local c = W[self].vertexColor
    if not c then
        return 1, 1, 1, 1
    end
    return c[1], c[2], c[3], c[4]
end
function Region:IsObjectLoaded() return true end

-- Named objects become globals. Those a Blizzard template creates are set
-- quietly; those the addon names itself go through G's __newindex and are
-- recorded as globals the addon created.
local buildingTemplate = 0
local function publish(name, object)
    if buildingTemplate > 0 then
        rawset(G, name, object)
    else
        G[name] = object
    end
end

local AnimatableObject = {}
function AnimatableObject:CreateAnimationGroup(name, template)
    local group = newWidget("AnimationGroup", name, self)
    W[group].animations = {}
    W[group].looping = "NONE"
    local groups = W[self].animationGroups or {}
    W[self].animationGroups = groups
    groups[#groups + 1] = group
    if name then
        publish(name, group)
    end
    return group
end
function AnimatableObject:GetAnimationGroups()
    return unpack(W[self].animationGroups or {})
end
function AnimatableObject:StopAnimating()
    for _, group in ipairs(W[self].animationGroups or {}) do
        W[group].playing = false
    end
end
for name, fn in pairs(AnimatableObject) do
    Region[name] = fn
end

-- ---- Texture ---------------------------------------------------------------

local TextureBase = class("TextureBase")

function TextureBase:SetColorTexture(r, g, b, a)
    local s = W[self]
    s.color = { colorArguments("SetColorTexture", r, g, b, a) }
    s.texture, s.atlas = nil, nil
end
function TextureBase:SetTexture(texture)
    local s = W[self]
    if texture ~= nil and type(texture) ~= "string" and type(texture) ~= "number" then
        fail("SetTexture(): file path, file ID or nil expected, got " .. type(texture), 2)
    end
    s.texture, s.color, s.atlas = texture, nil, nil
    return true
end
function TextureBase:GetTexture() return W[self].texture end
function TextureBase:SetAtlas(atlas)
    local s = W[self]
    if atlas ~= nil and type(atlas) ~= "string" then
        fail("SetAtlas(): atlas name expected, got " .. type(atlas), 2)
    end
    s.atlas, s.color, s.texture = atlas, nil, nil
end
function TextureBase:GetAtlas() return W[self].atlas end
function TextureBase:SetTexCoord(...)
    local n = select("#", ...)
    if n ~= 4 and n ~= 8 then
        fail("SetTexCoord(): 4 or 8 coordinates expected, got " .. n, 2)
    end
    W[self].texCoord = { ... }
end
function TextureBase:GetTexCoord()
    local c = W[self].texCoord
    if c and #c == 8 then
        return unpack(c)
    end
    local l, r, t, b = 0, 1, 0, 1
    if c then
        l, r, t, b = c[1], c[2], c[3], c[4]
    end
    return l, t, l, b, r, t, r, b
end
function TextureBase:SetBlendMode(mode)
    local modes = { DISABLE = true, BLEND = true, ALPHAKEY = true, ADD = true, MOD = true }
    if not modes[mode] then
        fail("SetBlendMode(): unknown blend mode " .. tostring(mode), 2)
    end
    W[self].blendMode = mode
end
function TextureBase:GetBlendMode() return W[self].blendMode or "BLEND" end
function TextureBase:SetDesaturated(desaturated) W[self].desaturated = desaturated and true or false end
function TextureBase:IsDesaturated() return W[self].desaturated == true end
function TextureBase:SetSnapToPixelGrid(snap) W[self].snapToPixelGrid = snap and true or false end
function TextureBase:IsSnappingToPixelGrid() return W[self].snapToPixelGrid ~= false end
function TextureBase:SetTexelSnappingBias(bias) W[self].texelSnappingBias = numberArgument("SetTexelSnappingBias", bias, "bias") end
function TextureBase:GetTexelSnappingBias() return W[self].texelSnappingBias or 0.5 end
function TextureBase:SetVertexOffset(vertexIndex, x, y)
    if type(vertexIndex) ~= "number" or vertexIndex < 1 or vertexIndex > 4 then
        fail("SetVertexOffset(): vertex index must be 1..4, got " .. tostring(vertexIndex), 2)
    end
    local offsets = W[self].vertexOffsets or {}
    W[self].vertexOffsets = offsets
    offsets[vertexIndex] = { numberArgument("SetVertexOffset", x, "x"), numberArgument("SetVertexOffset", y, "y") }
end
function TextureBase:GetVertexOffset(vertexIndex)
    local offset = (W[self].vertexOffsets or {})[vertexIndex]
    if offset then
        return offset[1], offset[2]
    end
    return 0, 0
end
function TextureBase:SetRotation(radians) W[self].rotation = numberArgument("SetRotation", radians, "angle") end

-- ---- Line ---------------------------------------------------------------------
-- A line is drawn between two points, each an offset from a point of
-- another region - not anchored like a texture.

local Line = class("Line")

local function linePoint(fn, point, relativeTo, x, y)
    if type(point) ~= "string" then
        fail(fn .. "(): the relative point must be a string such as \"TOPLEFT\"", 3)
    end
    if type(relativeTo) ~= "table" or not W[relativeTo] then
        fail(fn .. "(): the region the point is relative to is missing", 3)
    end
    return {
        point = point, relativeTo = relativeTo,
        x = x == nil and 0 or numberArgument(fn, x, "x offset"),
        y = y == nil and 0 or numberArgument(fn, y, "y offset"),
    }
end
function Line:SetStartPoint(point, relativeTo, x, y)
    W[self].startPoint = linePoint("SetStartPoint", point, relativeTo, x, y)
end
function Line:SetEndPoint(point, relativeTo, x, y)
    W[self].endPoint = linePoint("SetEndPoint", point, relativeTo, x, y)
end
function Line:SetThickness(thickness)
    thickness = numberArgument("SetThickness", thickness, "thickness")
    if thickness <= 0 then
        fail("SetThickness(): thickness must be positive", 2)
    end
    W[self].thickness = thickness
end
function Line:GetThickness() return W[self].thickness or 0 end
function TextureBase:GetRotation() return W[self].rotation or 0 end

-- ---- Fonts and FontString ----------------------------------------------------

local function fontObject(method, font)
    -- Accepts a Font object or the name of one; returns the object.
    if type(font) == "string" then
        if not host.is_font(font) then
            fail(method .. "(): there is no font object named \"" .. font .. "\" in this client", 3)
        end
        return G[font]
    end
    if font ~= nil and not (isWidget(font) and W[font].type == "Font") then
        fail(method .. "(): font object, its name, or nil expected", 3)
    end
    return font
end

local function fontHeight(self)
    local s = W[self]
    if s.fontHeight then
        return s.fontHeight
    end
    if s.fontObject and s.fontObject ~= self then
        return fontHeight(s.fontObject)
    end
    return 12
end

local FontInstance = {}
function FontInstance:SetFontObject(font)
    W[self].fontObject = fontObject("SetFontObject", font)
end
function FontInstance:GetFontObject() return W[self].fontObject end
function FontInstance:SetFont(path, height, flags)
    if type(path) ~= "string" then
        fail("SetFont(): font file path expected, got " .. type(path), 2)
    end
    numberArgument("SetFont", height, "height")
    if height <= 0 then
        fail("SetFont(): height must be greater than 0", 2)
    end
    if flags ~= nil and type(flags) ~= "string" then
        fail("SetFont(): flags must be a string", 2)
    end
    local s = W[self]
    s.fontPath, s.fontHeight, s.fontFlags = path, height, flags or ""
    return true
end
function FontInstance:GetFont()
    local s = W[self]
    return s.fontPath or "Fonts\\FRIZQT__.TTF", fontHeight(self), s.fontFlags or ""
end
function FontInstance:SetTextColor(r, g, b, a)
    W[self].textColor = { colorArguments("SetTextColor", r, g, b, a) }
end
function FontInstance:GetTextColor()
    local c = W[self].textColor
    if not c then
        return 1, 1, 1, 1
    end
    return c[1], c[2], c[3], c[4]
end
function FontInstance:SetJustifyH(justify)
    if justify ~= "LEFT" and justify ~= "CENTER" and justify ~= "RIGHT" then
        fail("SetJustifyH(): LEFT, CENTER or RIGHT expected, got " .. tostring(justify), 2)
    end
    W[self].justifyH = justify
end
function FontInstance:GetJustifyH() return W[self].justifyH or "CENTER" end
function FontInstance:SetJustifyV(justify)
    if justify ~= "TOP" and justify ~= "MIDDLE" and justify ~= "BOTTOM" then
        fail("SetJustifyV(): TOP, MIDDLE or BOTTOM expected, got " .. tostring(justify), 2)
    end
    W[self].justifyV = justify
end
function FontInstance:GetJustifyV() return W[self].justifyV or "MIDDLE" end
function FontInstance:SetShadowColor(r, g, b, a) colorArguments("SetShadowColor", r, g, b, a) end
function FontInstance:SetShadowOffset(x, y)
    numberArgument("SetShadowOffset", x, "x")
    numberArgument("SetShadowOffset", y, "y")
end
function FontInstance:SetSpacing(spacing) numberArgument("SetSpacing", spacing, "spacing") end

local Font = class("Font")
for name, fn in pairs(FontInstance) do
    Font[name] = fn
end
function Font:GetFontHeight() return fontHeight(self) end
function Font:CopyFontObject(source)
    local from = W[fontObject("CopyFontObject", source)]
    local s = W[self]
    s.fontPath, s.fontHeight, s.fontFlags, s.textColor = from.fontPath, from.fontHeight, from.fontFlags, from.textColor
end

local function textArgument(method, text)
    if text == nil then
        return nil
    end
    if type(text) == "number" then
        return tostring(text)
    end
    if type(text) ~= "string" then
        fail(method .. "(): text must be a string, number or nil, got " .. type(text), 3)
    end
    return text
end

local function plainText(text)
    -- Text as displayed: colour codes and texture escapes take no room.
    text = text or ""
    text = text:gsub("|c%x%x%x%x%x%x%x%x", ""):gsub("|r", "")
    text = text:gsub("|T.-|t", "  "):gsub("|A.-|a", "  "):gsub("||", "|")
    return text
end
M.plain_text = plainText

local FontString = class("FontString")
for name, fn in pairs(FontInstance) do
    FontString[name] = fn
end
function FontString:SetText(text)
    W[self].text = textArgument("SetText", text)
end
function FontString:SetFormattedText(pattern, ...)
    if type(pattern) ~= "string" then
        fail("SetFormattedText(): format string expected", 2)
    end
    W[self].text = sformat(pattern, ...)
end
function FontString:GetText() return W[self].text end
function FontString:ClearText() W[self].text = nil end
function FontString:SetTextToFit(text) W[self].text = textArgument("SetTextToFit", text) end
function FontString:GetFontHeight() return fontHeight(self) end
function FontString:GetLineHeight() return fontHeight(self) end
function FontString:GetStringHeight()
    local _, lines = string.gsub(W[self].text or "", "\n", "\n")
    return fontHeight(self) * (lines + 1)
end
function FontString:GetStringWidth()
    -- A crude metric: half an em per character of the longest line.
    local longest = 0
    for line in (plainText(W[self].text) .. "\n"):gmatch("(.-)\n") do
        if #line > longest then
            longest = #line
        end
    end
    return longest * fontHeight(self) * 0.5
end
FontString.GetUnboundedStringWidth = FontString.GetStringWidth
function FontString:GetWrappedWidth() return FontString.GetStringWidth(self) end
function FontString:GetNumLines()
    local _, lines = string.gsub(W[self].text or "", "\n", "\n")
    return lines + 1
end
function FontString:IsTruncated()
    local width = W[self].width
    return width ~= nil and width > 0 and FontString.GetStringWidth(self) > width
end
function FontString:SetWordWrap(wrap) W[self].wordWrap = wrap and true or false end
function FontString:CanWordWrap() return W[self].wordWrap ~= false end
function FontString:SetNonSpaceWrap(wrap) W[self].nonSpaceWrap = wrap and true or false end
function FontString:SetMaxLines(lines) W[self].maxLines = numberArgument("SetMaxLines", lines, "line count") end
function FontString:GetMaxLines() return W[self].maxLines or 0 end
function FontString:SetTextHeight(height) W[self].fontHeight = numberArgument("SetTextHeight", height, "height") end
function FontString:SetTextScale(scale) W[self].textScale = numberArgument("SetTextScale", scale, "scale") end
function FontString:GetTextScale() return W[self].textScale or 1 end

naturalSize = function(widget)
    local s = W[widget]
    if s.type == "FontString" then
        return FontString.GetStringWidth(widget), FontString.GetStringHeight(widget)
    end
    return nil
end

-- ---- Frame -------------------------------------------------------------------

local STRATA = {
    PARENT = 0, BACKGROUND = 1, LOW = 2, MEDIUM = 3, HIGH = 4, DIALOG = 5,
    FULLSCREEN = 6, FULLSCREEN_DIALOG = 7, TOOLTIP = 8,
}

local eventRegistry = {}   -- event -> array of frames

local Frame = class("Frame")
for name, fn in pairs(AnimatableObject) do
    Frame[name] = fn
end
Frame.SetAlpha = setAlpha
function Frame:GetAlpha() return W[self].alpha end
function Frame:GetEffectiveAlpha() return effectiveAlpha(self) end
Frame.SetScale = setScale
function Frame:GetScale() return W[self].scale end
function Frame:GetEffectiveScale() return effectiveScale(self) end
function Frame:SetIgnoreParentScale(ignore) W[self].ignoreParentScale = ignore and true or false end
function Frame:IsIgnoringParentScale() return W[self].ignoreParentScale == true end
function Frame:SetIgnoreParentAlpha(ignore) W[self].ignoreParentAlpha = ignore and true or false end
function Frame:IsIgnoringParentAlpha() return W[self].ignoreParentAlpha == true end
Frame.Show, Frame.Hide, Frame.SetShown = ScriptRegion.Show, ScriptRegion.Hide, ScriptRegion.SetShown
Frame.IsShown, Frame.IsVisible = ScriptRegion.IsShown, ScriptRegion.IsVisible
function Frame:IsObjectLoaded() return true end

function Frame:SetFrameStrata(strata)
    if not STRATA[strata] then
        fail(describe(self) .. ":SetFrameStrata(): unknown strata " .. tostring(strata), 2)
    end
    W[self].strata = strata
end
function Frame:GetFrameStrata()
    local s = W[self]
    while s do
        if s.strata and s.strata ~= "PARENT" then
            return s.strata
        end
        s = s.parent and W[s.parent]
    end
    return "MEDIUM"
end
function Frame:SetFrameLevel(level)
    numberArgument("SetFrameLevel", level, "level")
    if level < 0 or level > 10000 then
        fail(describe(self) .. ":SetFrameLevel(): level must be 0..10000, got " .. tostring(level), 2)
    end
    W[self].level = floor(level)
end
function Frame:GetFrameLevel()
    local s = W[self]
    if s.level then
        return s.level
    end
    if s.parent and W[s.parent] and infoOf(W[s.parent].type).isA.Frame then
        return Frame.GetFrameLevel(s.parent) + 1
    end
    return 0
end
function Frame:SetFixedFrameStrata(fixed) W[self].fixedStrata = fixed and true or false end
function Frame:SetFixedFrameLevel(fixed) W[self].fixedLevel = fixed and true or false end
function Frame:SetToplevel(top) W[self].toplevel = top and true or false end
function Frame:IsToplevel() return W[self].toplevel == true end
function Frame:Raise() end
function Frame:Lower() end
function Frame:SetID(id) W[self].id = numberArgument("SetID", id, "id") end
function Frame:GetID() return W[self].id or 0 end
function Frame:SetClampedToScreen(clamped) W[self].clamped = clamped and true or false end
function Frame:IsClampedToScreen() return W[self].clamped == true end
function Frame:SetMovable(movable) W[self].movable = movable and true or false end
function Frame:IsMovable() return W[self].movable == true end
function Frame:SetResizable(resizable) W[self].resizable = resizable and true or false end
function Frame:IsResizable() return W[self].resizable == true end
function Frame:SetUserPlaced(placed)
    if placed and not W[self].movable then
        fail(describe(self) .. ":SetUserPlaced(): the frame is not movable", 2)
    end
    W[self].userPlaced = placed and true or false
end
function Frame:IsUserPlaced() return W[self].userPlaced == true end
function Frame:SetDontSavePosition(dontSave) W[self].dontSavePosition = dontSave and true or false end
function Frame:GetDontSavePosition() return W[self].dontSavePosition == true end
function Frame:SetHitRectInsets(left, right, top, bottom)
    W[self].hitRectInsets = {
        numberArgument("SetHitRectInsets", left, "left"), numberArgument("SetHitRectInsets", right, "right"),
        numberArgument("SetHitRectInsets", top, "top"), numberArgument("SetHitRectInsets", bottom, "bottom"),
    }
end
function Frame:GetHitRectInsets()
    local insets = W[self].hitRectInsets or { 0, 0, 0, 0 }
    return insets[1], insets[2], insets[3], insets[4]
end
function Frame:SetClipsChildren(clips) W[self].clipsChildren = clips and true or false end
function Frame:DoesClipChildren() return W[self].clipsChildren == true end
function Frame:EnableKeyboard(enable) W[self].keyboard = enable and true or false end
function Frame:IsKeyboardEnabled() return W[self].keyboard == true end
function Frame:SetPropagateKeyboardInput(propagate) W[self].propagateKeyboard = propagate and true or false end

local MOUSE_BUTTONS = { LeftButton = true, RightButton = true, MiddleButton = true, Button4 = true, Button5 = true }
function Frame:RegisterForDrag(...)
    local buttons = {}
    for i = 1, select("#", ...) do
        local button = select(i, ...)
        if not MOUSE_BUTTONS[button] then
            fail("RegisterForDrag(): unknown mouse button " .. tostring(button), 2)
        end
        buttons[button] = true
    end
    W[self].dragButtons = buttons
end
function Frame:StartMoving()
    local s = W[self]
    if not s.movable then
        fail(describe(self) .. ":StartMoving(): the frame is not movable (call SetMovable(true) first)", 2)
    end
    s.moving = true
end
function Frame:StartSizing()
    local s = W[self]
    if not s.resizable then
        fail(describe(self) .. ":StartSizing(): the frame is not resizable", 2)
    end
    s.moving = true
end
function Frame:StopMovingOrSizing()
    local s = W[self]
    if s.moving and s.pendingMove then
        -- The client re-anchors a user-moved frame to the screen.
        local left, bottom, width, height = rectOf(self)
        if left then
            local scale = effectiveScale(self)
            local screenWidth, screenHeight = screenSize()
            local newLeft = left + s.pendingMove[1]
            local newTop = bottom + height + s.pendingMove[2]
            if s.clamped then
                newLeft = math.max(0, math.min(screenWidth - width, newLeft))
                newTop = math.min(screenHeight, math.max(height, newTop))
            end
            local x = newLeft / scale
            local y = (newTop - screenHeight) / scale
            s.points = { { point = "TOPLEFT", relativeTo = nil, relativePoint = "TOPLEFT", x = x, y = y } }
        end
    end
    s.moving, s.pendingMove = nil, nil
end

function Frame:SetAttribute(name, value)
    local attributes = W[self].attributes or {}
    W[self].attributes = attributes
    attributes[name] = value
    runScript(self, "OnAttributeChanged", name, value)
end
function Frame:GetAttribute(name)
    return (W[self].attributes or {})[name]
end

function Frame:GetParent() return W[self].parent end
function Frame:GetChildren() return unpack(W[self].children) end
function Frame:GetNumChildren() return #W[self].children end
function Frame:GetRegions() return unpack(W[self].regions) end
function Frame:GetNumRegions() return #W[self].regions end

-- events
local function checkEvent(method, event)
    if type(event) ~= "string" then
        fail(method .. "(): event name must be a string", 3)
    end
    if not host.is_event(event) then
        fail(method .. "(): attempt to register unknown event \"" .. event .. "\"", 3)
    end
end
function Frame:RegisterEvent(event)
    checkEvent("RegisterEvent", event)
    local s = W[self]
    s.events = s.events or {}
    if s.events[event] then
        return false
    end
    s.events[event] = true
    local list = eventRegistry[event] or {}
    eventRegistry[event] = list
    list[#list + 1] = self
    return true
end
function Frame:RegisterUnitEvent(event, ...)
    checkEvent("RegisterUnitEvent", event)
    for i = 1, select("#", ...) do
        if type((select(i, ...))) ~= "string" then
            fail("RegisterUnitEvent(): unit tokens must be strings", 2)
        end
    end
    local registered = Frame.RegisterEvent(self, event)
    W[self].unitEvents = W[self].unitEvents or {}
    W[self].unitEvents[event] = { ... }
    return registered
end
function Frame:UnregisterEvent(event)
    checkEvent("UnregisterEvent", event)
    local s = W[self]
    if not (s.events and s.events[event]) then
        return false
    end
    s.events[event] = nil
    local list = eventRegistry[event]
    for index = #list, 1, -1 do
        if list[index] == self then
            table.remove(list, index)
        end
    end
    return true
end
function Frame:UnregisterAllEvents()
    local s = W[self]
    for event in pairs(s.events or {}) do
        local list = eventRegistry[event]
        for index = #list, 1, -1 do
            if list[index] == self then
                table.remove(list, index)
            end
        end
    end
    s.events = {}
end
function Frame:IsEventRegistered(event)
    local s = W[self]
    return (s.events and s.events[event]) == true
end

-- regions
local function createRegion(self, widgetType, name, layer, subLevel)
    local region = newWidget(widgetType, name, self)
    W[region].layer = layer or "ARTWORK"
    W[region].subLevel = subLevel or 0
    if name then
        publish(name, region)
    end
    return region
end

local applyTemplates   -- defined with templates below

function Frame:CreateTexture(name, layer, inherits, subLevel)
    if name ~= nil and type(name) ~= "string" then
        fail("CreateTexture(): name must be a string or nil", 2)
    end
    checkLayer("CreateTexture", layer, subLevel)
    local texture = createRegion(self, "Texture", name, layer, subLevel)
    if inherits then
        applyTemplates(texture, inherits, "CreateTexture")
    end
    return texture
end
function Frame:CreateMaskTexture(name, layer, inherits, subLevel)
    checkLayer("CreateMaskTexture", layer, subLevel)
    return createRegion(self, "MaskTexture", name, layer, subLevel)
end
function Frame:CreateLine(name, layer, inherits, subLevel)
    checkLayer("CreateLine", layer, subLevel)
    return createRegion(self, "Line", name, layer, subLevel)
end
function Frame:CreateFontString(name, layer, inherits)
    if name ~= nil and type(name) ~= "string" then
        fail("CreateFontString(): name must be a string or nil", 2)
    end
    checkLayer("CreateFontString", layer)
    local fontString = createRegion(self, "FontString", name, layer, 0)
    if inherits ~= nil then
        if type(inherits) ~= "string" then
            fail("CreateFontString(): inherits must be the name of a font object or template", 2)
        end
        if host.is_font(inherits) then
            W[fontString].fontObject = G[inherits]
        else
            applyTemplates(fontString, inherits, "CreateFontString")
        end
    end
    return fontString
end

-- ---- Button -------------------------------------------------------------------

local Button = class("Button")

local function buttonFontString(self, create)
    local s = W[self]
    if not s.fontString then
        local keyed = rawget(self, "Text")
        if isWidget(keyed) and W[keyed].type == "FontString" then
            s.fontString = keyed
        elseif create then
            s.fontString = createRegion(self, "FontString", nil, "ARTWORK", 0)
        end
    end
    return s.fontString
end

function Button:SetText(text)
    text = textArgument("SetText", text)
    W[self].text = text
    W[buttonFontString(self, true)].text = text
end
function Button:SetFormattedText(pattern, ...)
    Button.SetText(self, sformat(pattern, ...))
end
function Button:GetText() return W[self].text end
function Button:GetFontString() return buttonFontString(self, false) end
function Button:SetFontString(fontString)
    if not (isWidget(fontString) and W[fontString].type == "FontString") then
        fail("SetFontString(): FontString expected", 2)
    end
    W[self].fontString = fontString
end
function Button:GetTextWidth()
    local fontString = buttonFontString(self, false)
    return fontString and FontString.GetStringWidth(fontString) or 0
end
function Button:GetTextHeight()
    local fontString = buttonFontString(self, false)
    return fontString and FontString.GetStringHeight(fontString) or 0
end
local function setEnabled(self, enabled)
    enabled = enabled and true or false
    local s = W[self]
    local was = s.disabled ~= true
    s.disabled = not enabled
    if was ~= enabled then
        runScript(self, enabled and "OnEnable" or "OnDisable")
    end
end
function Button:Enable() setEnabled(self, true) end
function Button:Disable() setEnabled(self, false) end
function Button:SetEnabled(enabled) setEnabled(self, enabled) end
function Button:IsEnabled() return W[self].disabled ~= true end
function Button:SetMotionScriptsWhileDisabled(enabled) W[self].motionWhileDisabled = enabled and true or false end
function Button:GetMotionScriptsWhileDisabled() return W[self].motionWhileDisabled == true end
function Button:RegisterForClicks(...)
    local valid = { AnyUp = true, AnyDown = true }
    for button in pairs(MOUSE_BUTTONS) do
        valid[button .. "Up"], valid[button .. "Down"] = true, true
    end
    for i = 1, select("#", ...) do
        if not valid[(select(i, ...))] then
            fail("RegisterForClicks(): unknown click type " .. tostring((select(i, ...))), 2)
        end
    end
    W[self].clicks = { ... }
end
function Button:SetButtonState(buttonState, locked)
    if buttonState ~= "NORMAL" and buttonState ~= "PUSHED" and buttonState ~= "DISABLED" then
        fail("SetButtonState(): NORMAL, PUSHED or DISABLED expected", 2)
    end
    W[self].buttonState = buttonState
end
function Button:GetButtonState() return W[self].buttonState or (W[self].disabled and "DISABLED" or "NORMAL") end
function Button:LockHighlight() W[self].highlightLocked = true end
function Button:UnlockHighlight() W[self].highlightLocked = false end

local function buttonTexture(kind)
    Button["Set" .. kind .. "Texture"] = function(self, texture)
        local s = W[self]
        local key = "texture" .. kind
        if isWidget(texture) then
            s[key] = texture
        else
            if not s[key] then
                s[key] = createRegion(self, "Texture", nil, "ARTWORK", 0)
            end
            TextureBase.SetTexture(s[key], texture)
        end
    end
    Button["Get" .. kind .. "Texture"] = function(self)
        return W[self]["texture" .. kind]
    end
    Button["Set" .. kind .. "Atlas"] = function(self, atlas)
        local s = W[self]
        local key = "texture" .. kind
        if not s[key] then
            s[key] = createRegion(self, "Texture", nil, "ARTWORK", 0)
        end
        TextureBase.SetAtlas(s[key], atlas)
    end
end
for _, kind in ipairs({ "Normal", "Pushed", "Disabled", "Highlight" }) do
    buttonTexture(kind)
end
for _, kind in ipairs({ "Normal", "Disabled", "Highlight" }) do
    Button["Set" .. kind .. "FontObject"] = function(self, font)
        W[self]["font" .. kind] = fontObject("Set" .. kind .. "FontObject", font)
    end
    Button["Get" .. kind .. "FontObject"] = function(self)
        return W[self]["font" .. kind]
    end
end
function Button:SetPushedTextOffset(x, y)
    numberArgument("SetPushedTextOffset", x, "x")
    numberArgument("SetPushedTextOffset", y, "y")
end

local function click(self, mouseButton, down)
    local s = W[self]
    if s.disabled then
        return false
    end
    mouseButton = mouseButton or "LeftButton"
    if not MOUSE_BUTTONS[mouseButton] then
        fail("click: unknown mouse button " .. tostring(mouseButton), 2)
    end
    if infoOf(s.type).isA.CheckButton then
        s.checked = not s.checked
    end
    runScript(self, "PreClick", mouseButton, down or false)
    runScript(self, "OnClick", mouseButton, down or false)
    runScript(self, "PostClick", mouseButton, down or false)
    return true
end
function Button:Click(mouseButton, down)
    click(self, mouseButton, down)
end

local CheckButton = class("CheckButton")
function CheckButton:SetChecked(checked) W[self].checked = checked and true or false end
function CheckButton:GetChecked() return W[self].checked == true end

-- ---- Slider / StatusBar ---------------------------------------------------------

local Slider = class("Slider")

local function sliderSet(self, value, userInput)
    local s = W[self]
    local low, high = s.minValue or 0, s.maxValue or 0
    if value < low then
        value = low
    elseif value > high then
        value = high
    end
    if s.value ~= value then
        s.value = value
        runScript(self, "OnValueChanged", value, userInput and true or false)
    end
end

function Slider:SetMinMaxValues(low, high)
    numberArgument("SetMinMaxValues", low, "minimum")
    numberArgument("SetMinMaxValues", high, "maximum")
    if high < low then
        fail(describe(self) .. ":SetMinMaxValues(): maximum " .. high .. " is below minimum " .. low, 2)
    end
    local s = W[self]
    local changed = s.minValue ~= low or s.maxValue ~= high
    s.minValue, s.maxValue = low, high
    if changed then
        runScript(self, "OnMinMaxChanged", low, high)
    end
    sliderSet(self, s.value or low, false)
end
function Slider:GetMinMaxValues() return W[self].minValue or 0, W[self].maxValue or 0 end
function Slider:SetValue(value)
    sliderSet(self, numberArgument("SetValue", value, "value"), false)
end
function Slider:GetValue() return W[self].value or W[self].minValue or 0 end
function Slider:SetValueStep(step) W[self].valueStep = numberArgument("SetValueStep", step, "step") end
function Slider:GetValueStep() return W[self].valueStep or 0 end
function Slider:SetObeyStepOnDrag(obey) W[self].obeyStep = obey and true or false end
function Slider:GetObeyStepOnDrag() return W[self].obeyStep == true end
function Slider:SetStepsPerPage(steps) W[self].stepsPerPage = numberArgument("SetStepsPerPage", steps, "steps") end
function Slider:GetStepsPerPage() return W[self].stepsPerPage or 1 end
function Slider:SetOrientation(orientation)
    if orientation ~= "HORIZONTAL" and orientation ~= "VERTICAL" then
        fail("SetOrientation(): HORIZONTAL or VERTICAL expected", 2)
    end
    W[self].orientation = orientation
end
function Slider:GetOrientation() return W[self].orientation or "VERTICAL" end
function Slider:SetThumbTexture(texture)
    local s = W[self]
    if isWidget(texture) then
        s.thumb = texture
    else
        s.thumb = s.thumb or createRegion(self, "Texture", nil, "ARTWORK", 0)
        TextureBase.SetTexture(s.thumb, texture)
    end
end
function Slider:GetThumbTexture() return W[self].thumb end
function Slider:Enable() setEnabled(self, true) end
function Slider:Disable() setEnabled(self, false) end
function Slider:SetEnabled(enabled) setEnabled(self, enabled) end
function Slider:IsEnabled() return W[self].disabled ~= true end
function Slider:IsDraggingThumb() return false end

local StatusBar = class("StatusBar")
function StatusBar:SetMinMaxValues(low, high)
    numberArgument("SetMinMaxValues", low, "minimum")
    numberArgument("SetMinMaxValues", high, "maximum")
    local s = W[self]
    s.minValue, s.maxValue = low, high
    runScript(self, "OnMinMaxChanged", low, high)
end
function StatusBar:GetMinMaxValues() return W[self].minValue or 0, W[self].maxValue or 1 end
function StatusBar:SetValue(value)
    numberArgument("SetValue", value, "value")
    local s = W[self]
    local low, high = s.minValue or 0, s.maxValue or 1
    value = math.max(low, math.min(high, value))
    if s.value ~= value then
        s.value = value
        runScript(self, "OnValueChanged", value)
    end
end
function StatusBar:GetValue() return W[self].value or W[self].minValue or 0 end
function StatusBar:SetStatusBarTexture(texture)
    local s = W[self]
    if isWidget(texture) then
        s.barTexture = texture
    else
        s.barTexture = s.barTexture or createRegion(self, "Texture", nil, "ARTWORK", 0)
        TextureBase.SetTexture(s.barTexture, texture)
    end
end
function StatusBar:GetStatusBarTexture() return W[self].barTexture end
function StatusBar:SetStatusBarColor(r, g, b, a)
    W[self].barColor = { colorArguments("SetStatusBarColor", r, g, b, a) }
end
function StatusBar:GetStatusBarColor()
    local c = W[self].barColor or { 1, 1, 1, 1 }
    return c[1], c[2], c[3], c[4]
end
function StatusBar:SetOrientation(orientation)
    if orientation ~= "HORIZONTAL" and orientation ~= "VERTICAL" then
        fail("SetOrientation(): HORIZONTAL or VERTICAL expected", 2)
    end
    W[self].orientation = orientation
end
function StatusBar:GetOrientation() return W[self].orientation or "HORIZONTAL" end
function StatusBar:SetReverseFill(reverse) W[self].reverseFill = reverse and true or false end
function StatusBar:GetReverseFill() return W[self].reverseFill == true end

-- ---- EditBox -----------------------------------------------------------------------

local focusedEditBox

local EditBox = class("EditBox")
for name, fn in pairs(FontInstance) do
    EditBox[name] = fn
end

local function editSetText(self, text, userInput)
    local s = W[self]
    text = text or ""
    if s.maxLetters and s.maxLetters > 0 and #text > s.maxLetters then
        text = text:sub(1, s.maxLetters)
    end
    if s.numeric and not text:match("^%d*$") then
        text = text:gsub("%D", "")
    end
    if (s.text or "") ~= text then
        s.text = text
        s.cursor = #text
        if not userInput then
            runScript(self, "OnTextSet")
        end
        runScript(self, "OnTextChanged", userInput and true or false)
    end
end

function EditBox:SetText(text)
    editSetText(self, textArgument("SetText", text), false)
end
function EditBox:GetText() return W[self].text or "" end
function EditBox:GetDisplayText() return W[self].text or "" end
function EditBox:HasText() return (W[self].text or "") ~= "" end
function EditBox:SetNumber(number)
    editSetText(self, tostring(numberArgument("SetNumber", number, "number")), false)
end
function EditBox:GetNumber() return tonumber(W[self].text) or 0 end
function EditBox:GetNumLetters() return #(W[self].text or "") end
function EditBox:Insert(text)
    editSetText(self, (W[self].text or "") .. textArgument("Insert", text), false)
end
function EditBox:SetFocus()
    if focusedEditBox == self then
        return
    end
    if focusedEditBox then
        local previous = focusedEditBox
        focusedEditBox = nil
        runScript(previous, "OnEditFocusLost")
    end
    focusedEditBox = self
    runScript(self, "OnEditFocusGained")
end
function EditBox:ClearFocus()
    if focusedEditBox == self then
        focusedEditBox = nil
        runScript(self, "OnEditFocusLost")
    end
end
function EditBox:HasFocus() return focusedEditBox == self end
function EditBox:SetAutoFocus(auto) W[self].autoFocus = auto and true or false end
function EditBox:IsAutoFocus() return W[self].autoFocus ~= false end
function EditBox:SetMaxLetters(count) W[self].maxLetters = numberArgument("SetMaxLetters", count, "count") end
function EditBox:GetMaxLetters() return W[self].maxLetters or 0 end
function EditBox:SetMaxBytes(count) W[self].maxBytes = numberArgument("SetMaxBytes", count, "count") end
function EditBox:SetNumeric(numeric) W[self].numeric = numeric and true or false end
function EditBox:IsNumeric() return W[self].numeric == true end
function EditBox:SetMultiLine(multi) W[self].multiLine = multi and true or false end
function EditBox:IsMultiLine() return W[self].multiLine == true end
function EditBox:SetCursorPosition(position) W[self].cursor = numberArgument("SetCursorPosition", position, "position") end
function EditBox:GetCursorPosition() return W[self].cursor or 0 end
function EditBox:HighlightText(first, last) W[self].highlight = { first or 0, last or -1 } end
function EditBox:ClearHighlightText() W[self].highlight = nil end
function EditBox:SetTextInsets(left, right, top, bottom)
    W[self].textInsets = {
        numberArgument("SetTextInsets", left, "left"), numberArgument("SetTextInsets", right, "right"),
        numberArgument("SetTextInsets", top, "top"), numberArgument("SetTextInsets", bottom, "bottom"),
    }
end
function EditBox:GetTextInsets()
    local insets = W[self].textInsets or { 0, 0, 0, 0 }
    return insets[1], insets[2], insets[3], insets[4]
end
function EditBox:Enable() setEnabled(self, true) end
function EditBox:Disable() setEnabled(self, false) end
function EditBox:SetEnabled(enabled) setEnabled(self, enabled) end
function EditBox:IsEnabled() return W[self].disabled ~= true end

-- ---- ScrollFrame ---------------------------------------------------------------------

local ScrollFrame = class("ScrollFrame")
function ScrollFrame:SetScrollChild(child)
    if not (isWidget(child) and infoOf(W[child].type).isA.Frame) then
        fail("SetScrollChild(): frame expected", 2)
    end
    W[self].scrollChild = child
    ScriptRegion.SetParent(child, self)
end
function ScrollFrame:GetScrollChild() return W[self].scrollChild end
local function scrollRange(self, vertical)
    local child = W[self].scrollChild
    if not child then
        return 0
    end
    local childWidth, childHeight = sizeOf(child)
    local width, height = sizeOf(self)
    if vertical then
        return math.max(0, childHeight - height)
    end
    return math.max(0, childWidth - width)
end
function ScrollFrame:GetVerticalScrollRange() return scrollRange(self, true) end
function ScrollFrame:GetHorizontalScrollRange() return scrollRange(self, false) end
function ScrollFrame:SetVerticalScroll(offset)
    numberArgument("SetVerticalScroll", offset, "offset")
    local s = W[self]
    if s.verticalScroll ~= offset then
        s.verticalScroll = offset
        runScript(self, "OnVerticalScroll", offset)
    end
end
function ScrollFrame:GetVerticalScroll() return W[self].verticalScroll or 0 end
function ScrollFrame:SetHorizontalScroll(offset)
    numberArgument("SetHorizontalScroll", offset, "offset")
    local s = W[self]
    if s.horizontalScroll ~= offset then
        s.horizontalScroll = offset
        runScript(self, "OnHorizontalScroll", offset)
    end
end
function ScrollFrame:GetHorizontalScroll() return W[self].horizontalScroll or 0 end
function ScrollFrame:UpdateScrollChildRect() end

-- ---- MessageFrame (the chat frame) --------------------------------------------------

local MessageFrame = class("MessageFrame")
for name, fn in pairs(FontInstance) do
    MessageFrame[name] = fn
end
function MessageFrame:AddMessage(text, r, g, b)
    log.printed[#log.printed + 1] = textArgument("AddMessage", text) or ""
end
function MessageFrame:Clear() end

-- ---- GameTooltip ---------------------------------------------------------------------

local TOOLTIP_ANCHORS = {
    ANCHOR_TOP = true, ANCHOR_RIGHT = true, ANCHOR_BOTTOM = true, ANCHOR_LEFT = true,
    ANCHOR_TOPRIGHT = true, ANCHOR_BOTTOMRIGHT = true, ANCHOR_TOPLEFT = true, ANCHOR_BOTTOMLEFT = true,
    ANCHOR_CURSOR = true, ANCHOR_CURSOR_RIGHT = true, ANCHOR_CURSOR_LEFT = true, ANCHOR_PRESERVE = true,
    ANCHOR_NONE = true,
}

local GameTooltip = class("GameTooltip")
function GameTooltip:SetOwner(owner, anchor, x, y)
    if not isWidget(owner) then
        fail("GameTooltip:SetOwner(): owner must be a region", 2)
    end
    if anchor ~= nil and not TOOLTIP_ANCHORS[anchor] then
        fail("GameTooltip:SetOwner(): unknown anchor " .. tostring(anchor), 2)
    end
    local s = W[self]
    s.owner, s.anchor, s.lines = owner, anchor or "ANCHOR_TOPRIGHT", {}
    setShown(self, false)
end
function GameTooltip:GetOwner() return W[self].owner end
function GameTooltip:IsOwned(owner) return W[self].owner ~= nil and W[self].owner == owner end
function GameTooltip:ClearLines()
    W[self].lines = {}
    runScript(self, "OnTooltipCleared")
end
function GameTooltip:NumLines() return #(W[self].lines or {}) end
function GameTooltip:SetText(text, r, g, b, a, wrap)
    W[self].lines = { { left = textArgument("SetText", text) or "" } }
    if W[self].owner then
        setShown(self, true)
    end
end
function GameTooltip:AddLine(text, r, g, b, wrap)
    local lines = W[self].lines or {}
    W[self].lines = lines
    lines[#lines + 1] = { left = textArgument("AddLine", text) or "" }
end
function GameTooltip:AddDoubleLine(left, right)
    local lines = W[self].lines or {}
    W[self].lines = lines
    lines[#lines + 1] = { left = textArgument("AddDoubleLine", left) or "", right = textArgument("AddDoubleLine", right) or "" }
end
function GameTooltip:Show()
    -- A tooltip without an owner does not appear.
    if W[self].owner then
        setShown(self, true)
    end
end
function GameTooltip:Hide()
    setShown(self, false)
    W[self].owner = nil
end
function GameTooltip:SetMinimumWidth(width) W[self].minimumWidth = numberArgument("SetMinimumWidth", width, "width") end
function GameTooltip:GetMinimumWidth() return W[self].minimumWidth or 0 end
function GameTooltip:SetPadding(...) end
function GameTooltip:FadeOut() GameTooltip.Hide(self) end

function M.tooltip_lines(tooltip)
    local out = {}
    for index, line in ipairs(W[tooltip].lines or {}) do
        out[index] = line.right and (line.left .. "\t" .. line.right) or line.left
    end
    return out
end

-- ---- Animations -----------------------------------------------------------------------

local AnimationGroup = class("AnimationGroup")
AnimationGroup.SetScript, AnimationGroup.GetScript = ScriptRegion.SetScript, ScriptRegion.GetScript
AnimationGroup.HookScript, AnimationGroup.HasScript = ScriptRegion.HookScript, ScriptRegion.HasScript

local playing = {}   -- animation groups currently playing

function AnimationGroup:CreateAnimation(animationType, name, template)
    animationType = animationType or "Animation"
    local info = infoOf(animationType)
    if not (info and info.isA.Animation) then
        fail("CreateAnimation(): unknown animation type " .. tostring(animationType), 2)
    end
    local animation = newWidget(animationType, name, self)
    W[animation].duration, W[animation].order, W[animation].startDelay, W[animation].endDelay = 0, 1, 0, 0
    local animations = W[self].animations
    animations[#animations + 1] = animation
    return animation
end
function AnimationGroup:GetAnimations() return unpack(W[self].animations) end
function AnimationGroup:GetDuration()
    -- Animations of one order run together; orders run one after another.
    local perOrder, total = {}, 0
    for _, animation in ipairs(W[self].animations) do
        local a = W[animation]
        local length = a.startDelay + a.duration + a.endDelay
        perOrder[a.order] = math.max(perOrder[a.order] or 0, length)
    end
    for _, length in pairs(perOrder) do
        total = total + length
    end
    return total
end
function AnimationGroup:Play()
    local s = W[self]
    if not s.playing then
        s.playing, s.paused, s.elapsed = true, false, 0
        playing[#playing + 1] = self
        runScript(self, "OnPlay")
    end
end
local function stopGroup(self, finished)
    local s = W[self]
    if s.playing then
        s.playing = false
        for index = #playing, 1, -1 do
            if playing[index] == self then
                table.remove(playing, index)
            end
        end
        if finished then
            runScript(self, "OnFinished", false)
        else
            runScript(self, "OnStop", true)
        end
    end
end
function AnimationGroup:Stop() stopGroup(self, false) end
function AnimationGroup:Finish() W[self].finishing = true end
function AnimationGroup:Pause() W[self].paused = true end
function AnimationGroup:Restart()
    stopGroup(self, false)
    AnimationGroup.Play(self)
end
function AnimationGroup:IsPlaying() return W[self].playing == true and not W[self].paused end
function AnimationGroup:IsPaused() return W[self].paused == true end
function AnimationGroup:IsDone() return W[self].playing ~= true end
function AnimationGroup:SetLooping(looping)
    if looping ~= "NONE" and looping ~= "REPEAT" and looping ~= "BOUNCE" then
        fail("SetLooping(): NONE, REPEAT or BOUNCE expected, got " .. tostring(looping), 2)
    end
    W[self].looping = looping
end
function AnimationGroup:GetLooping() return W[self].looping end
function AnimationGroup:SetToFinalAlpha(final) W[self].toFinalAlpha = final and true or false end
function AnimationGroup:IsSetToFinalAlpha() return W[self].toFinalAlpha == true end
function AnimationGroup:GetProgress()
    local duration = AnimationGroup.GetDuration(self)
    return duration > 0 and math.min(1, (W[self].elapsed or 0) / duration) or 1
end
function AnimationGroup:GetElapsed() return W[self].elapsed or 0 end

local function advanceAnimations(elapsed)
    for _, group in ipairs({ unpack(playing) }) do
        local s = W[group]
        if s.playing and not s.paused then
            s.elapsed = s.elapsed + elapsed
            runScript(group, "OnUpdate", elapsed)
            local duration = AnimationGroup.GetDuration(group)
            if s.elapsed >= duration then
                if s.looping == "NONE" or s.finishing then
                    s.finishing = nil
                    stopGroup(group, true)
                else
                    s.elapsed = duration > 0 and s.elapsed % duration or 0
                    runScript(group, "OnLoop", "FORWARD")
                end
            end
        end
    end
end

local Animation = class("Animation")
Animation.SetScript, Animation.GetScript = ScriptRegion.SetScript, ScriptRegion.GetScript
Animation.HookScript, Animation.HasScript = ScriptRegion.HookScript, ScriptRegion.HasScript
function Animation:SetDuration(seconds)
    numberArgument("SetDuration", seconds, "duration")
    if seconds < 0 then
        fail("SetDuration(): duration must not be negative", 2)
    end
    W[self].duration = seconds
end
function Animation:GetDuration() return W[self].duration end
function Animation:SetOrder(order)
    numberArgument("SetOrder", order, "order")
    if order < 1 or order > 100 then
        fail("SetOrder(): order must be 1..100", 2)
    end
    W[self].order = order
end
function Animation:GetOrder() return W[self].order end
function Animation:SetStartDelay(seconds) W[self].startDelay = numberArgument("SetStartDelay", seconds, "delay") end
function Animation:GetStartDelay() return W[self].startDelay end
function Animation:SetEndDelay(seconds) W[self].endDelay = numberArgument("SetEndDelay", seconds, "delay") end
function Animation:GetEndDelay() return W[self].endDelay end
function Animation:SetSmoothing(smoothing)
    local valid = { NONE = true, IN = true, OUT = true, IN_OUT = true, OUT_IN = true }
    if not valid[smoothing] then
        fail("SetSmoothing(): unknown smoothing " .. tostring(smoothing), 2)
    end
    W[self].smoothing = smoothing
end
function Animation:GetSmoothing() return W[self].smoothing or "NONE" end
function Animation:GetRegionParent()
    local group = W[self].parent
    return group and W[group].parent
end
function Animation:IsPlaying()
    local group = W[self].parent
    return group ~= nil and W[group].playing == true
end

local Alpha = class("Alpha")
function Alpha:SetFromAlpha(alpha) W[self].fromAlpha = numberArgument("SetFromAlpha", alpha, "alpha") end
function Alpha:GetFromAlpha() return W[self].fromAlpha or 0 end
function Alpha:SetToAlpha(alpha) W[self].toAlpha = numberArgument("SetToAlpha", alpha, "alpha") end
function Alpha:GetToAlpha() return W[self].toAlpha or 0 end

----------------------------------------------------------------------------
-- Templates and mixins
----------------------------------------------------------------------------
-- Templates are instantiated from their shape in the snapshot: keyed child
-- regions and frames are created (`button.Text`, `slider.Thumb`, ...), and
-- every method of the template's mixins becomes a field on the frame. The
-- mixin methods do nothing unless `mixinImpl` gives them behaviour.

local mixinImpl = {}
M.mixin_impl = mixinImpl

-- host.mixin_members(name) -> { method = owner }: the methods of a mixin,
-- including those of the mixins it was created from, and who defines each.
local mixinMembers = {}
local function membersOf(mixinName)
    local members = mixinMembers[mixinName]
    if members == nil then
        members = host.mixin_members(mixinName) or false
        mixinMembers[mixinName] = members
    end
    return members or nil
end

local function mixinMethod(mixinName, owner, methodName)
    for _, source in ipairs({ mixinName, owner }) do
        local impl = mixinImpl[source]
        if impl and impl[methodName] ~= nil then
            return impl[methodName]
        end
    end
    local key = owner .. ":" .. methodName
    return function()
        log.mixin_stub[key] = (log.mixin_stub[key] or 0) + 1
    end
end

local function applyMixin(target, mixinName)
    local members = membersOf(mixinName)
    if members then
        for name, owner in pairs(members) do
            rawset(target, name, mixinMethod(mixinName, owner, name))
        end
    end
    local impl = mixinImpl[mixinName]
    if impl then
        -- Behaviour the mock adds beyond the method names it knows from the
        -- snapshot (inherited callback-registry methods, constants).
        for name, value in pairs(impl) do
            if name ~= "__init" and rawget(target, name) == nil then
                rawset(target, name, value)
            end
        end
    end
end

local function initMixin(target, mixinName)
    local impl = mixinImpl[mixinName]
    if impl and impl.__init then
        impl.__init(target)
    end
end

local shapeCache = {}
local function templateShape(name)
    local shape = shapeCache[name]
    if shape == nil then
        shape = host.template(name) or false
        shapeCache[name] = shape
    end
    return shape or nil
end

local buildShape

local function createChild(parent, shape)
    local childType = shape.t
    local native = host.native_type(childType)
    if not native then
        return nil      -- a widget type the mock does not know (rare)
    end
    local info = infoOf(native)
    local parentState = W[parent]
    local name
    if shape.n then
        local owner = parentState.name
        if shape.n:find("$parent", 1, true) then
            name = owner and shape.n:gsub("%$parent", owner) or nil
        else
            name = shape.n
        end
    end
    local child
    if native == "AnimationGroup" then
        child = AnimatableObject.CreateAnimationGroup(parent, name)
    elseif info.isA.Animation then
        if parentState.type ~= "AnimationGroup" then
            return nil
        end
        child = AnimationGroup.CreateAnimation(parent, native, name)
    elseif info.isA.Frame then
        child = newWidget(native, name, parent)
        W[child].objectType = childType
    else
        child = createRegion(parent, native, name, "ARTWORK", 0)
    end
    if name and info.isA.Frame then
        rawset(G, name, child)
    end
    if shape.h then
        W[child].shown = false
    end
    if shape.k then
        rawset(parent, shape.k, child)
        W[child].parentKey = shape.k
    end
    if shape.a then
        local array = rawget(parent, shape.a)
        if type(array) ~= "table" then
            array = {}
            rawset(parent, shape.a, array)
        end
        array[#array + 1] = child
    end
    buildShape(child, shape, {})
    return child
end

-- <Size> of a template in Blizzard's XML: a frame made from it has that
-- size until the addon gives it another. The snapshot does not carry sizes,
-- so this lists only the templates whose own size the addon's layout relies
-- on (it sets the size of everything else itself), each with its source.
local templateSize = {
    -- Blizzard_SharedXML/Mainline/SharedUIPanelTemplates.xml: <Size x="24" y="24"/>
    UIPanelCloseButtonNoScripts = { 24, 24 },
}
M.template_size = templateSize

local function applyTemplate(widget, templateName, seen)
    if seen[templateName] then
        return
    end
    seen[templateName] = true
    local shape = templateShape(templateName)
    local size = templateSize[templateName]
    if size and not (W[widget].width or W[widget].height) then
        W[widget].width, W[widget].height = size[1], size[2]
    end
    if shape then
        buildShape(widget, shape, seen)
    elseif host.is_font(templateName) then
        if W[widget].type == "FontString" or W[widget].type == "EditBox" then
            W[widget].fontObject = G[templateName]
        end
    end
    -- Anything else was inherited by Blizzard's own XML from a file this
    -- snapshot does not cover; there is nothing to build from it.
end

local buildShapeInner

buildShape = function(widget, shape, seen)
    buildingTemplate = buildingTemplate + 1
    local ok, err = pcall(buildShapeInner, widget, shape, seen)
    buildingTemplate = buildingTemplate - 1
    if not ok then
        error(err, 0)
    end
end

buildShapeInner = function(widget, shape, seen)
    for _, inherited in ipairs(shape.i or {}) do
        applyTemplate(widget, inherited, seen)
    end
    if shape.x and shape.t ~= W[widget].type then
        -- An intrinsic builds on another intrinsic or widget type.
        local base = templateShape(shape.t)
        if base and not seen[shape.t] then
            seen[shape.t] = true
            buildShape(widget, base, seen)
        end
    end
    for _, mixinName in ipairs(shape.m or {}) do
        applyMixin(widget, mixinName)
    end
    for _, childShape in ipairs(shape.c or {}) do
        createChild(widget, childShape)
    end
    -- Mixin set-up runs last: it may wire up the children just created.
    for _, mixinName in ipairs(shape.m or {}) do
        initMixin(widget, mixinName)
    end
end

applyTemplates = function(widget, templates, method)
    if type(templates) ~= "string" then
        fail(method .. "(): template must be a string of template names", 3)
    end
    local seen = {}
    for templateName in templates:gmatch("[^,%s]+") do
        local shape = templateShape(templateName)
        if not shape then
            local lod = host.lod(templateName)
            if lod then
                fail(method .. "(): template \"" .. templateName .. "\" is defined by the load-on-demand addon "
                    .. lod .. " and does not exist until that addon is loaded", 3)
            end
            fail(method .. "(): there is no template named \"" .. templateName .. "\" in this client", 3)
        end
        local native = host.native_type(shape.t)
        if native and not infoOf(W[widget].type).isA[native] then
            fail(method .. "(): template \"" .. templateName .. "\" is for " .. native
                .. " widgets and cannot be used on a " .. W[widget].type, 3)
        end
        applyTemplate(widget, templateName, seen)
    end
end

local function createFrame(frameType, name, parent, template, id)
    if type(frameType) ~= "string" then
        fail("CreateFrame(): frame type must be a string", 2)
    end
    local native = host.native_type(frameType)
    local info = native and infoOf(native)
    if not (info and info.isA.Frame) then
        fail("CreateFrame(): unknown frame type \"" .. frameType .. "\"", 2)
    end
    if name ~= nil and type(name) ~= "string" then
        fail("CreateFrame(): name must be a string or nil", 2)
    end
    if type(parent) == "string" then
        local found = G[parent]
        if not isWidget(found) then
            fail("CreateFrame(): no frame named \"" .. parent .. "\" to use as parent", 2)
        end
        parent = found
    end
    if parent ~= nil and not (isWidget(parent) and infoOf(W[parent].type).isA.Frame) then
        fail("CreateFrame(): parent must be a frame or nil", 2)
    end
    local frame = newWidget(native, name, parent)
    W[frame].objectType = frameType
    if native ~= frameType then
        -- An intrinsic frame type (EventFrame, DropdownButton ...).
        buildShape(frame, templateShape(frameType), { [frameType] = true })
    end
    if template ~= nil then
        applyTemplates(frame, template, "CreateFrame")
    end
    if id ~= nil then
        W[frame].id = id
    end
    if name then
        G[name] = frame     -- through __newindex: recorded as created by the addon
    end
    return frame
end
define("CreateFrame", function(frameType, name, parent, template, id)
    return createFrame(frameType, name, parent, template, id)
end)

----------------------------------------------------------------------------
-- Behaviour for the mixins the addon relies on
----------------------------------------------------------------------------

-- BackdropTemplateMixin: remember what was set.
do
    local Backdrop = {}
    mixinImpl.BackdropTemplateMixin = Backdrop
    function Backdrop:SetBackdrop(info)
        if info ~= nil and type(info) ~= "table" then
            fail("SetBackdrop(): table or nil expected", 2)
        end
        rawset(self, "backdropInfo", info)
    end
    function Backdrop:GetBackdrop() return rawget(self, "backdropInfo") end
    function Backdrop:ClearBackdrop() rawset(self, "backdropInfo", nil) end
    function Backdrop:ApplyBackdrop() end
    function Backdrop:OnBackdropLoaded() end
    function Backdrop:OnBackdropSizeChanged() end
    function Backdrop:SetBackdropColor(r, g, b, a)
        W[self].backdropColor = { colorArguments("SetBackdropColor", r, g, b, a) }
    end
    function Backdrop:GetBackdropColor()
        local c = W[self].backdropColor or { 1, 1, 1, 1 }
        return c[1], c[2], c[3], c[4]
    end
    function Backdrop:SetBackdropBorderColor(r, g, b, a)
        W[self].backdropBorderColor = { colorArguments("SetBackdropBorderColor", r, g, b, a) }
    end
    function Backdrop:GetBackdropBorderColor()
        local c = W[self].backdropBorderColor or { 1, 1, 1, 1 }
        return c[1], c[2], c[3], c[4]
    end
end

-- CallbackRegistryMixin: RegisterCallback / TriggerEvent.
local CallbackRegistry = {}
mixinImpl.CallbackRegistryMixin = CallbackRegistry
do
    local function registry(self)
        local callbacks = rawget(self, "callbackTables")
        if not callbacks then
            callbacks = {}
            rawset(self, "callbackTables", callbacks)
        end
        return callbacks
    end
    local ownerSerial = 0
    function CallbackRegistry:OnLoad() registry(self) end
    function CallbackRegistry:RegisterCallback(event, callback, owner, ...)
        if type(event) ~= "string" then
            fail("RegisterCallback(): event must be a string (got " .. type(event) .. ")", 2)
        end
        if type(callback) ~= "function" then
            fail("RegisterCallback(): callback must be a function", 2)
        end
        local events = rawget(self, "Event")
        if type(events) == "table" and events[event] == nil then
            fail("RegisterCallback(): this object has no \"" .. event .. "\" callback event", 2)
        end
        if owner == nil then
            -- The client makes up an owner, and hands it to the callback
            -- like any other: the first argument is never the event's.
            ownerSerial = ownerSerial + 1
            owner = ownerSerial
        elseif type(owner) == "number" then
            fail("RegisterCallback(): a number as owner is reserved for the client", 2)
        end
        -- One callback per owner and event: registering again replaces it.
        CallbackRegistry.UnregisterCallback(self, event, owner)
        local callbacks = registry(self)
        callbacks[event] = callbacks[event] or {}
        table.insert(callbacks[event], { callback = callback, owner = owner, n = select("#", ...), ... })
        return owner
    end
    function CallbackRegistry:UnregisterCallback(event, owner)
        local list = registry(self)[event]
        for index = #(list or {}), 1, -1 do
            if list[index].owner == owner then
                table.remove(list, index)
            end
        end
    end
    function CallbackRegistry:TriggerEvent(event, ...)
        local list = registry(self)[event]
        if not list then
            return
        end
        for _, entry in ipairs({ unpack(list) }) do
            if entry.n > 0 then
                -- Registered with extra arguments: owner, those, the event's.
                local args = { entry.owner, unpack(entry, 1, entry.n) }
                local count = entry.n + 1
                for index = 1, select("#", ...) do
                    args[count + index] = (select(index, ...))
                end
                entry.callback(unpack(args, 1, count + select("#", ...)))
            else
                entry.callback(entry.owner, ...)
            end
        end
    end
    function CallbackRegistry:GenerateCallbackEvents(events)
        local map = rawget(self, "Event") or {}
        for _, event in ipairs(events) do
            map[event] = event
        end
        rawset(self, "Event", map)
    end
end

-- MinimalSliderWithSteppersMixin: a slider with stepper buttons and labels.
do
    local Stepper = {}
    mixinImpl.MinimalSliderWithSteppersMixin = Stepper
    Stepper.Event = { OnValueChanged = "OnValueChanged", OnInteractStart = "OnInteractStart", OnInteractEnd = "OnInteractEnd" }
    Stepper.Label = { Left = 1, Right = 2, Top = 3, Min = 4, Max = 5 }
    for name, fn in pairs(CallbackRegistry) do
        Stepper[name] = fn
    end
    function Stepper.__init(self)
        rawset(self, "Event", Stepper.Event)
        rawset(self, "Label", Stepper.Label)
        rawset(self, "sliderEnabled", true)
        local back, forward = rawget(self, "Back"), rawget(self, "Forward")
        if back then
            ScriptRegion.SetScript(back, "OnClick", function() self:OnStepperClicked(false) end)
        end
        if forward then
            ScriptRegion.SetScript(forward, "OnClick", function() self:OnStepperClicked(true) end)
        end
    end
    function Stepper:OnLoad() end
    function Stepper:OnStepperClicked(forward)
        local slider = self.Slider
        local step = slider:GetValueStep()
        local value = slider:GetValue() + (forward and step or -step)
        sliderSet(slider, value, true)
    end
    function Stepper:UpdateStepperStates()
        if not self.sliderEnabled then
            return
        end
        local slider = self.Slider
        local value = slider:GetValue()
        local low, high = slider:GetMinMaxValues()
        local step = slider:GetValueStep()
        self.Back:SetEnabled(not (value <= low + step * 0.5))
        self.Forward:SetEnabled(not (value >= high - step * 0.5))
    end
    function Stepper:Init(value, minValue, maxValue, steps, formatters)
        if type(value) ~= "number" or type(minValue) ~= "number" or type(maxValue) ~= "number"
            or type(steps) ~= "number" then
            fail("MinimalSliderWithSteppers:Init(value, min, max, steps, formatters): numbers expected", 2)
        end
        if steps <= 0 then
            fail("MinimalSliderWithSteppers:Init(): steps must be greater than 0", 2)
        end
        local slider = self.Slider
        slider:SetMinMaxValues(minValue, maxValue)
        slider:SetValueStep((maxValue - minValue) / steps)
        slider:SetValue(value)
        rawset(self, "formatters", formatters)
        self:FormatValue(value)
        self:UpdateStepperStates()
        slider:SetScript("OnValueChanged", function(_, newValue)
            self:FormatValue(newValue)
            self:UpdateStepperStates()
            self:TriggerEvent(Stepper.Event.OnValueChanged, newValue)
        end)
    end
    function Stepper:FormatValue(value)
        local formatters = rawget(self, "formatters")
        if not formatters then
            return
        end
        for labelID, formatter in pairs(formatters) do
            local label = self.Labels[labelID]
            if label then
                label:SetText(formatter(value))
                label:Show()
            end
        end
    end
    function Stepper:SetValue(value)
        self.Slider:SetValue(value)
    end
    function Stepper:SetEnabled(enabled)
        rawset(self, "sliderEnabled", enabled and true or false)
        self.Slider:SetEnabled(enabled)
        if enabled then
            self:UpdateStepperStates()
        else
            self.Back:SetEnabled(false)
            self.Forward:SetEnabled(false)
        end
    end
    function Stepper:IsSliderEnabled() return self.sliderEnabled end
    function Stepper:Release() self.Slider:SetScript("OnValueChanged", nil) end
end

-- ColorMixin, for CreateColor().
local Color = {}
mixinImpl.ColorMixin = Color
function Color:GetRGB() return self.r, self.g, self.b end
function Color:GetRGBA() return self.r, self.g, self.b, self.a end
function Color:SetRGBA(r, g, b, a) self.r, self.g, self.b, self.a = r, g, b, a end
function Color:SetRGB(r, g, b) self.r, self.g, self.b = r, g, b end
function Color:GenerateHexColor()
    return sformat("ff%02x%02x%02x", floor(self.r * 255 + 0.5), floor(self.g * 255 + 0.5), floor(self.b * 255 + 0.5))
end
function Color:GenerateHexColorMarkup() return "|c" .. Color.GenerateHexColor(self) end
function Color:WrapTextInColorCode(text) return "|c" .. Color.GenerateHexColor(self) .. tostring(text) .. "|r" end
function Color:IsEqualTo(other)
    return self.r == other.r and self.g == other.g and self.b == other.b and self.a == other.a
end
function Color:IsRGBEqualTo(other)
    return self.r == other.r and self.g == other.g and self.b == other.b
end
local function createColor(r, g, b, a)
    local color = { r = r, g = g, b = b, a = a or 1 }
    for name, fn in pairs(Color) do
        color[name] = fn
    end
    return color
end
define("CreateColor", function(r, g, b, a)
    numberArgument("CreateColor", r, "red")
    numberArgument("CreateColor", g, "green")
    numberArgument("CreateColor", b, "blue")
    return createColor(r, g, b, a)
end)

----------------------------------------------------------------------------
-- Blizzard Lua the addon may call
----------------------------------------------------------------------------

define("Clamp", function(value, low, high)
    if value > high then
        return high
    elseif value < low then
        return low
    end
    return value
end)
define("Lerp", function(from, to, amount)
    return from + (to - from) * amount
end)
define("Saturate", function(value)
    return G.Clamp(value, 0, 1)
end)
define("Round", function(value)
    if value < 0 then
        return math.ceil(value - 0.5)
    end
    return floor(value + 0.5)
end)
define("GenerateClosure", function(fn, ...)
    local n = select("#", ...)
    local bound = { ... }
    return function(...)
        local args = {}
        for i = 1, n do
            args[i] = bound[i]
        end
        local m = select("#", ...)
        for i = 1, m do
            args[n + i] = select(i, ...)
        end
        return fn(unpack(args, 1, n + m))
    end
end)
define("CopyTable", function(source, shallow)
    local copy = {}
    for key, value in pairs(source) do
        if type(value) == "table" and not shallow then
            copy[key] = G.CopyTable(value)
        else
            copy[key] = value
        end
    end
    return copy
end)
define("WrapTextInColorCode", function(text, colorHex)
    return "|c" .. colorHex .. text .. "|r"
end)

define("UISpecialFrames", {})
define("SlashCmdList", {})
define("StaticPopupDialogs", {})

define("StaticPopup_Show", function(which, textArg1, textArg2, data)
    local dialog = G.StaticPopupDialogs[which]
    if type(dialog) ~= "table" then
        fail("StaticPopup_Show(): no dialog named " .. tostring(which), 2)
    end
    local popup = { which = which, data = data, text_arg1 = textArg1, text_arg2 = textArg2, dialog = dialog }
    log.popups[#log.popups + 1] = popup
    if dialog.OnShow then
        dialog.OnShow(popup, data)
    end
    return popup
end)
define("StaticPopup_Hide", function(which)
    for index = #log.popups, 1, -1 do
        if log.popups[index].which == which then
            table.remove(log.popups, index)
        end
    end
end)

--- Press a button of the newest popup of that name (1 = accept, 2 = cancel).
function M.popup_click(which, buttonIndex)
    for index = #log.popups, 1, -1 do
        local popup = log.popups[index]
        if popup.which == which then
            table.remove(log.popups, index)
            local dialog = popup.dialog
            if buttonIndex == 1 then
                if dialog.OnAccept then
                    dialog.OnAccept(popup, popup.data)
                end
            elseif dialog.OnCancel then
                dialog.OnCancel(popup, popup.data, "clicked")
            end
            return true
        end
    end
    return false
end

-- Tooltip helpers.
define("GameTooltip_SetTitle", function(tooltip, text)
    tooltip:SetText(text)
end)
define("GameTooltip_AddNormalLine", function(tooltip, text)
    tooltip:AddLine(text)
end)
define("GameTooltip_AddHighlightLine", function(tooltip, text)
    tooltip:AddLine(text)
end)
define("GameTooltip_Hide", function()
    G.GameTooltip:Hide()
end)

-- Settings (the options window). Categories are registered by ID.
--
-- The window is laid out as Blizzard_SettingsPanel.xml lays it out: 920 x
-- 724 in the middle of the screen, the page area (Container.SettingsCanvas,
-- 665 x 601) to the right of the category list. An addon's canvas frame is
-- handled the way SettingsPanelMixin handles it:
--
--   * opening the window always selects its first (Blizzard) category,
--     which takes whatever canvas was showing off the page - SetParent(nil),
--     ClearAllPoints, Hide - and then calls OnRefresh on every canvas;
--   * selecting a canvas category parents the frame to the page area,
--     SetAllPoints, Show, OnRefresh - and only then shows the page area, so
--     OnRefresh can run before the frame is visible;
--   * selecting the category that is already showing does nothing.
local Settings = {}
do
    local categories, nextID = {}, 1
    M.settings_categories = categories

    local PANEL_WIDTH, PANEL_HEIGHT = 920, 724
    local CANVAS_LEFT, CANVAS_TOP, CANVAS_RIGHT, CANVAS_BOTTOM = 233, -76, -22, 47

    local panel, canvas   -- SettingsPanel and its Container.SettingsCanvas
    local current         -- the category whose page is showing

    local function settingsPanel()
        if not panel then
            panel = G.SettingsPanel
            local s = W[panel]
            s.width, s.height = PANEL_WIDTH, PANEL_HEIGHT
            s.points = { { point = "CENTER", relativeTo = G.UIParent, relativePoint = "CENTER", x = 0, y = 0 } }
            local container = newWidget("Frame", nil, panel)
            rawset(panel, "Container", container)
            W[container].parentKey = "Container"
            W[container].points = {
                { point = "TOPLEFT", relativeTo = panel, relativePoint = "TOPLEFT", x = CANVAS_LEFT, y = CANVAS_TOP },
                { point = "BOTTOMRIGHT", relativeTo = panel, relativePoint = "BOTTOMRIGHT", x = CANVAS_RIGHT, y = CANVAS_BOTTOM },
            }
            canvas = newWidget("Frame", nil, container)
            rawset(container, "SettingsCanvas", canvas)
            W[canvas].parentKey = "SettingsCanvas"
            ScriptRegion.SetAllPoints(canvas, container)
            W[canvas].shown = false
        end
        return panel
    end

    -- The client calls these under xpcall with its error handler.
    local function callRefresh(frame)
        local refresh = frame.OnRefresh
        if refresh ~= nil then
            local ok, err = pcall(refresh, frame)
            if not ok then
                currentErrorHandler(err)
            end
        end
    end

    local function refreshAllCanvases()
        for id = 1, nextID - 1 do
            local category = categories[id]
            if category.registered and category.frame then
                callRefresh(category.frame)
            end
        end
    end

    local function clearCurrentCanvas()
        local frame = current and current.frame
        current = nil
        if frame then
            ScriptRegion.SetParent(frame, nil)
            ScriptRegion.ClearAllPoints(frame)
            setShown(frame, false)
        end
    end

    local function openPanel()
        settingsPanel()
        if not W[panel].shown then
            setShown(panel, true)
            clearCurrentCanvas()
            setShown(canvas, false)
            refreshAllCanvases()
        end
    end

    local function selectCategory(category)
        openPanel()
        if current ~= category then
            clearCurrentCanvas()
            current = category
            local frame = category.frame
            if frame then
                ScriptRegion.SetParent(frame, canvas)
                ScriptRegion.ClearAllPoints(frame)
                ScriptRegion.SetAllPoints(frame, canvas)
                setShown(frame, true)
                callRefresh(frame)
                setShown(canvas, true)
            end
        end
    end

    function Settings.RegisterCanvasLayoutCategory(frame, name)
        if not (isWidget(frame) and infoOf(W[frame].type).isA.Frame) then
            fail("Settings.RegisterCanvasLayoutCategory(frame, name): frame expected", 2)
        end
        if type(name) ~= "string" or name == "" then
            fail("Settings.RegisterCanvasLayoutCategory(frame, name): name must be a non-empty string", 2)
        end
        local category = { ID = nextID, name = name, frame = frame, registered = false }
        nextID = nextID + 1
        applyMixin(category, "SettingsCategoryMixin")
        function category:GetID() return self.ID end
        function category:GetName() return self.name end
        categories[category.ID] = category
        return category, { frame = frame }
    end

    function Settings.RegisterAddOnCategory(category)
        if type(category) ~= "table" or categories[category.ID] ~= category then
            fail("Settings.RegisterAddOnCategory(): a category from Settings.Register...Category expected", 2)
        end
        category.registered = true
    end

    function Settings.OpenToCategory(categoryID, scrollToElementName)
        if type(categoryID) ~= "number" then
            fail("Settings.OpenToCategory(): category ID (a number, category:GetID()) expected, got "
                .. type(categoryID), 2)
        end
        if G.InCombatLockdown() then
            -- C_SettingsUtil.OpenSettingsPanel is restricted for addons.
            log.blocked[#log.blocked + 1] = "Settings.OpenToCategory"
            fail("Settings.OpenToCategory(): the settings window cannot be opened by an addon during combat", 2)
        end
        local category = categories[categoryID]
        if not (category and category.registered) then
            fail("Settings.OpenToCategory(): no registered category with ID " .. tostring(categoryID), 2)
        end
        log.settings_opened[#log.settings_opened + 1] = categoryID
        selectCategory(category)
    end

    --- The player opens the Settings window by hand (Esc > Options): it
    -- shows its first category, not an addon's page.
    function M.settings_open()
        openPanel()
    end

    --- The player closes the Settings window (Escape or its Close button).
    -- The page that was showing stays where it is, hidden with the window.
    function M.settings_close()
        if panel and W[panel].shown then
            setShown(panel, false)
        end
    end

    --- The player picks one of Blizzard's own categories in the open
    -- Settings window, which takes an addon's page off it.
    function M.settings_select_other()
        if panel and W[panel].shown then
            clearCurrentCanvas()
            setShown(canvas, false)
        end
    end

    --- The player picks a registered addon category in the category list
    -- (which, unlike an addon's Settings.OpenToCategory, works in combat).
    function M.settings_select(categoryID)
        local category = categories[categoryID]
        if not (category and category.registered) then
            fail("settings_select: no registered category with ID " .. tostring(categoryID), 2)
        end
        selectCategory(category)
    end
end

-- MenuUtil: context menus. The generator fills a description; the tests
-- read it back and press its entries (M.menu_click).
local MenuUtil = {}
do
    local descriptionMethods = {}
    local function entry(kind)
        return function(self, text, a, b, c)
            local item = { kind = kind, text = text }
            if kind == "button" then
                if a ~= nil and type(a) ~= "function" then
                    fail("menu CreateButton(text, callback): callback must be a function", 2)
                end
                item.callback, item.data = a, b
            elseif kind == "checkbox" or kind == "radio" then
                if type(a) ~= "function" or type(b) ~= "function" then
                    fail("menu Create" .. kind .. "(text, isSelected, setSelected): two functions expected", 2)
                end
                item.isSelected, item.setSelected, item.data = a, b, c
            end
            if kind ~= "divider" and kind ~= "spacer" and type(text) ~= "string" then
                fail("menu entry text must be a string", 2)
            end
            self.entries[#self.entries + 1] = item
            return item
        end
    end
    descriptionMethods.CreateTitle = entry("title")
    descriptionMethods.CreateButton = entry("button")
    descriptionMethods.CreateCheckbox = entry("checkbox")
    descriptionMethods.CreateRadio = entry("radio")
    descriptionMethods.CreateDivider = entry("divider")
    descriptionMethods.CreateSpacer = entry("spacer")
    M.menu_description_methods = descriptionMethods

    function MenuUtil.CreateContextMenu(ownerRegion, generator, ...)
        if ownerRegion ~= nil and not isWidget(ownerRegion) then
            fail("MenuUtil.CreateContextMenu(owner, generator): owner must be a region", 2)
        end
        if type(generator) ~= "function" then
            fail("MenuUtil.CreateContextMenu(owner, generator): generator must be a function", 2)
        end
        local root = setmetatable({ entries = {}, owner = ownerRegion }, { __index = descriptionMethods })
        generator(ownerRegion, root, ...)
        log.menus[#log.menus + 1] = root
        return root
    end
end

--- The newest context menu as a list of "kind:text[:checked]" strings.
function M.menu_entries()
    local menu = log.menus[#log.menus]
    local out = {}
    for index, item in ipairs(menu and menu.entries or {}) do
        local line = item.kind .. ":" .. tostring(item.text or "")
        if item.isSelected then
            line = line .. ":" .. tostring(item.isSelected(item.data) and true or false)
        end
        out[index] = line
    end
    return out
end

--- Choose an entry of the newest context menu by its text.
function M.menu_click(text)
    local menu = log.menus[#log.menus]
    for _, item in ipairs(menu and menu.entries or {}) do
        if item.text == text then
            if item.kind == "button" and item.callback then
                M.call(item.callback, item.data, nil, menu)
                return true
            elseif item.setSelected then
                M.call(item.setSelected, item.data, nil, menu)
                return true
            end
        end
    end
    fail("menu_click: the menu has no entry \"" .. tostring(text) .. "\"", 2)
end

----------------------------------------------------------------------------
-- Blizzard globals created on demand
----------------------------------------------------------------------------

-- A Lua table Blizzard defines (Settings, MenuUtil ...): the functions the
-- mock implements work, the others the client has raise when called, and a
-- name the client does not have reads as nil and is recorded.
local luaTableImpl = {
    Settings = Settings,
    MenuUtil = MenuUtil,
}
M.lua_table_impl = luaTableImpl

local function luaTable(name)
    local members = {}
    for _, member in ipairs(host.members(name) or {}) do
        members[member] = true
    end
    local result = {}
    for member, fn in pairs(luaTableImpl[name]) do
        result[member] = fn
    end
    return setmetatable(result, {
        __index = function(t, key)
            local full = name .. "." .. tostring(key)
            if members[key] then
                log.unimplemented[full] = true
                local stub = strictStub(full)
                rawset(t, key, stub)
                return stub
            end
            log.undefined[full] = (log.undefined[full] or 0) + 1
            return nil
        end,
    })
end

local valueImpl = {
    Settings = function() return luaTable("Settings") end,
    MenuUtil = function() return luaTable("MenuUtil") end,
    DEFAULT_CHAT_FRAME = function()
        return G.ChatFrame1
    end,
    WOW_PROJECT_ID = function() return 1 end,
    WOW_PROJECT_MAINLINE = function() return 1 end,
}
M.value_impl = valueImpl

local KNOWN_COLORS = {
    NORMAL_FONT_COLOR = { 1, 0.82, 0 },
    HIGHLIGHT_FONT_COLOR = { 1, 1, 1 },
    RED_FONT_COLOR = { 1, 0.125, 0.125 },
    GREEN_FONT_COLOR = { 0.125, 1, 0.125 },
    GRAY_FONT_COLOR = { 0.5, 0.5, 0.5 },
    DISABLED_FONT_COLOR = { 0.5, 0.5, 0.5 },
    YELLOW_FONT_COLOR = { 1, 1, 0 },
    ORANGE_FONT_COLOR = { 1, 0.5, 0.25 },
    WHITE_FONT_COLOR = { 1, 1, 1 },
}

local function blizzardFrame(name, definition)
    local native = host.native_type(definition.t)
    if not (native and infoOf(native)) then
        return nil
    end
    local info = infoOf(native)
    local parent
    if info.isA.Frame and name ~= "UIParent" and name ~= "WorldFrame" then
        parent = rawget(G, "UIParent")
    end
    local frame
    if info.isA.Frame then
        frame = newWidget(native, name, parent)
        W[frame].objectType = definition.t
        rawset(G, name, frame)      -- before building: children may refer to it
        if native ~= definition.t and templateShape(definition.t) then
            buildShape(frame, templateShape(definition.t), { [definition.t] = true })
        end
        buildShape(frame, definition, {})
        -- Blizzard's windows start closed.
        if name ~= "UIParent" and name ~= "WorldFrame" and name ~= "ChatFrame1" and name ~= "Minimap" then
            W[frame].shown = false
        end
    else
        return nil
    end
    return frame
end

local function constantsTable(name, members)
    local constants = {}
    for index, member in ipairs(members) do
        constants[member] = index
    end
    -- A constant this client does not have reads as nil, as in the game,
    -- and is recorded like an undefined global.
    return setmetatable(constants, {
        __index = function(_, key)
            local full = name .. "." .. tostring(key)
            log.undefined[full] = (log.undefined[full] or 0) + 1
            return nil
        end,
    })
end

materialize = function(name)
    local kind = kindOf(name)
    if not kind then
        return nil
    end
    if kind == "font" then
        local font = newWidget("Font", name, nil)
        return font
    elseif kind == "frame" then
        local frame = blizzardFrame(name, host.frame(name))
        if frame == nil then
            log.unimplemented[name] = true
        end
        return frame
    elseif kind == "namespace" then
        return makeNamespace(name)
    elseif kind == "api" or kind == "lua_function" or kind == "c" then
        log.unimplemented[name] = true
        return strictStub(name)
    elseif kind == "lua_value" then
        if valueImpl[name] then
            return valueImpl[name]()
        end
        local members = host.members(name)
        if members then
            if name:find("Mixin$") then
                local mixin = {}
                applyMixin(mixin, name)
                return mixin
            end
            local allCaps = true
            for _, member in ipairs(members) do
                if member:find("%l") then
                    allCaps = false
                    break
                end
            end
            if allCaps then
                return constantsTable(name, members)
            end
        end
        log.unimplemented[name] = true
        return nil
    elseif kind == "referenced" then
        -- A global string or a colour constant.
        if name:find("_COLOR$") then
            local c = KNOWN_COLORS[name] or { 1, 1, 1 }
            return createColor(c[1], c[2], c[3], 1)
        elseif name:find("_COLOR_CODE$") then
            return "|cffffffff"
        end
        return name
    elseif kind == "enum_root" then
        return setmetatable({}, {
            __index = function(t, key)
                if not host.is_enum(key) then
                    log.undefined["Enum." .. tostring(key)] = (log.undefined["Enum." .. tostring(key)] or 0) + 1
                    return nil
                end
                -- Values are not in the snapshot: hand out stable numbers.
                local counter = 0
                local enum = setmetatable({}, {
                    __index = function(e, field)
                        counter = counter + 1
                        rawset(e, field, counter)
                        return counter
                    end,
                })
                rawset(t, key, enum)
                return enum
            end,
        })
    elseif kind:sub(1, 4) == "lod:" then
        log.lod[name] = kind:sub(5)
        return nil
    end
    return nil
end

-- The frames every addon can count on.
do
    local world = newWidget("Frame", "WorldFrame", nil)
    ScriptRegion.SetAllPoints(world, nil)
    define("WorldFrame", world)
    local ui = newWidget("Frame", "UIParent", nil)
    ScriptRegion.SetAllPoints(ui, nil)
    define("UIParent", ui)
end

----------------------------------------------------------------------------
-- Driving the client (called from Python)
----------------------------------------------------------------------------

local function traceback(message)
    if type(message) ~= "string" then
        message = tostring(message)
    end
    if message:find("stack traceback:", 1, true) then
        return message      -- raised by a nested M.call: already has one
    end
    return debug.traceback(message, 2)
end

--- Call `fn(...)`; on error raise a message that includes the Lua traceback.
function M.call(fn, ...)
    local n = select("#", ...)
    local args = { ... }
    local results = { xpcall(function()
        return fn(unpack(args, 1, n))
    end, traceback) }
    if not results[1] then
        error(results[2], 0)
    end
    return unpack(results, 2, table.maxn(results))
end

function M.set_global(name, value)
    rawset(G, name, value)
end

function M.get_global(name)
    return rawget(G, name)
end

function M.new_table()
    return {}
end

function M.declare(name)
    log.declared[name] = true
end

--- Load one addon file the way the client does: (addonName, privateTable).
function M.load_file(source, chunkName, addonName, ns)
    local chunk, err = loadstring(source, "@" .. chunkName)
    if not chunk then
        error(err, 0)
    end
    setfenv(chunk, G)
    return M.call(chunk, addonName, ns)
end

--- Run a chunk of test code inside the addon environment.
function M.run(source, ...)
    local chunk, err = loadstring(source, "=test")
    if not chunk then
        error(err, 0)
    end
    setfenv(chunk, G)
    return M.call(chunk, ...)
end

function M.fire_event(event, ...)
    if not host.is_event(event) then
        fail("fire_event: unknown event " .. tostring(event), 2)
    end
    local list = eventRegistry[event]
    if not list then
        return 0
    end
    local n = select("#", ...)
    local args = { ... }
    local count = 0
    for _, frame in ipairs({ unpack(list) }) do
        local s = W[frame]
        if s.events and s.events[event] and s.scripts.OnEvent then
            local handler = s.scripts.OnEvent
            M.call(handler, frame, event, unpack(args, 1, n))
            count = count + 1
        end
    end
    return count
end

-- "1 days, 2 hours, 3 minutes, 4 seconds", as ChatFrameUtil.TimeBreakDown
-- and TIME_DAYHOURMINUTESECOND put it.
local function timeBreakDown(seconds)
    return sformat("%d days, %d hours, %d minutes, %d seconds",
        floor(seconds / 86400), floor(seconds % 86400 / 3600), floor(seconds % 3600 / 60), seconds % 60)
end

--- The server's answer to RequestTimePlayed(). Every chat window that
-- shows system messages prints it (ChatFrameMixin:SystemEventHandler ->
-- ChatFrameUtil.DisplayTimePlayed) - there is no telling who asked - and
-- every frame registered for TIME_PLAYED_MSG gets the event.
function M.time_played_message()
    local total, thisLevel = floor(state.played), floor(state.playedThisLevel)
    log.printed[#log.printed + 1] = "Total time played: " .. timeBreakDown(total)
    log.printed[#log.printed + 1] = "Time played this level: " .. timeBreakDown(thisLevel)
    M.fire_event("TIME_PLAYED_MSG", total, thisLevel)
end

--- One rendered frame: advance the clocks, run timers, OnUpdate and animations.
function M.run_frame(elapsed)
    -- Rounded to the microsecond so that thousands of 0.1 s steps do not
    -- drift (the epoch is a large number to keep adding small ones to).
    state.clock = floor((state.clock + elapsed) * 1e6 + 0.5) / 1e6
    state.uptime = floor((state.uptime + elapsed) * 1e6 + 0.5) / 1e6
    -- Time in the game is time played.
    state.played = floor((state.played + elapsed) * 1e6 + 0.5) / 1e6
    state.playedThisLevel = floor((state.playedThisLevel + elapsed) * 1e6 + 0.5) / 1e6
    while playedAnswersDue > 0 do
        playedAnswersDue = playedAnswersDue - 1
        M.time_played_message()
    end
    M.call(runTimers)
    local count = #allWidgets
    for index = 1, count do
        local widget = allWidgets[index]
        local s = W[widget]
        local handler = s.scripts.OnUpdate
        if handler and s.type ~= "AnimationGroup" and not infoOf(s.type).isA.Animation and isVisible(widget) then
            M.call(handler, widget, elapsed)
        end
    end
    M.call(advanceAnimations, elapsed)
end

--- Run a widget's script handler as the client would on user input.
function M.fire_script(widget, scriptName, ...)
    local s = W[widget]
    if not s then
        fail("fire_script: not a widget", 2)
    end
    if not infoOf(s.type).scripts[scriptName] then
        fail("fire_script: " .. describe(widget) .. " has no " .. scriptName .. " script type", 2)
    end
    local handler = s.scripts[scriptName]
    if not handler then
        return false
    end
    M.call(handler, widget, ...)
    return true
end

function M.has_script(widget, scriptName)
    local s = W[widget]
    return s ~= nil and s.scripts[scriptName] ~= nil
end

function M.click(widget, mouseButton)
    if not isA(widget, "Button") then
        fail("click: " .. describe(widget) .. " is not a button", 2)
    end
    if not isVisible(widget) then
        fail("click: " .. describe(widget) .. " is not visible", 2)
    end
    return M.call(click, widget, mouseButton, false)
end

--- Move the mouse over / off a region.
function M.hover(widget, entering)
    local s = W[widget]
    s.mouseOver = entering and true or false
    if s.disabled and not s.motionWhileDisabled and isA(widget, "Button") then
        return false
    end
    return M.fire_script(widget, entering and "OnEnter" or "OnLeave", false)
end

--- Drag a movable frame by (dx, dy) screen units with the left button.
function M.drag(widget, dx, dy)
    local s = W[widget]
    if not (s.dragButtons and s.dragButtons.LeftButton) then
        fail("drag: " .. describe(widget) .. " is not registered for left-button drags", 2)
    end
    M.fire_script(widget, "OnDragStart", "LeftButton")
    local started = s.moving == true
    if started then
        s.pendingMove = { dx, dy }
    end
    M.fire_script(widget, "OnDragStop")
    if s.moving then
        fail("drag: " .. describe(widget) .. " is still being moved after OnDragStop", 2)
    end
    return started
end

--- Type into an edit box as the user would (focus, text, optional Enter).
-- Returns false for a disabled box: it does not take the keyboard focus.
function M.type_text(editBox, text, pressEnter)
    if not isA(editBox, "EditBox") then
        fail("type_text: " .. describe(editBox) .. " is not an edit box", 2)
    end
    if W[editBox].disabled then
        return false
    end
    M.call(EditBox.SetFocus, editBox)
    M.call(editSetText, editBox, text, true)
    if pressEnter then
        M.fire_script(editBox, "OnEnterPressed")
    end
    return true
end

--- Move a slider's thumb as the user would.
function M.slide(slider, value)
    if not isA(slider, "Slider") then
        fail("slide: " .. describe(slider) .. " is not a slider", 2)
    end
    if W[slider].disabled then
        return false
    end
    M.call(sliderSet, slider, value, true)
    return true
end

function M.scroll_wheel(widget, delta)
    return M.fire_script(widget, "OnMouseWheel", delta)
end

--- Type a slash command into the chat box, e.g. M.slash("/walka start").
function M.slash(text)
    local command, rest = text:match("^(%S+)%s*(.-)$")
    if not command then
        fail("slash: empty command", 2)
    end
    command = command:lower()
    for name, value in next, G do
        if type(name) == "string" and type(value) == "string" and value:lower() == command then
            local key = name:match("^SLASH_(.-)%d+$")
            if key then
                local handler = G.SlashCmdList[key]
                if type(handler) ~= "function" then
                    fail("slash: " .. name .. " is defined but SlashCmdList[\"" .. key .. "\"] is not a function", 2)
                end
                M.call(handler, rest, nil)
                return true
            end
        end
    end
    fail("slash: no addon registered " .. command, 2)
end

function M.set_screen(width, height)
    state.physicalWidth, state.physicalHeight = width, height
    M.fire_event("DISPLAY_SIZE_CHANGED")
end

function M.set_ui_scale(scale)
    W[G.UIParent].scale = scale
    M.fire_event("UI_SCALE_CHANGED")
end

--- Details of a widget for assertions.
function M.inspect(widget)
    local s = W[widget]
    if not s then
        return nil
    end
    local left, bottom, width, height = rectOf(widget)
    return {
        type = s.type,
        name = s.name,
        shown = s.shown,
        visible = isVisible(widget),
        alpha = effectiveAlpha(widget),
        scale = effectiveScale(widget),
        layer = s.layer,
        subLevel = s.subLevel,
        text = s.text,
        color = s.color,
        texture = s.texture,
        atlas = s.atlas,
        left = left, bottom = bottom, width = width, height = height,
        strata = s.strata,
        level = s.level,
        parent = s.parent,
        serial = s.serial,
        disabled = s.disabled == true,
        checked = s.checked == true,
        value = s.value,
        mouse = s.mouse == true,
        -- lines
        startX = s.startPoint and s.startPoint.x, startY = s.startPoint and s.startPoint.y,
        endX = s.endPoint and s.endPoint.x, endY = s.endPoint and s.endPoint.y,
        thickness = s.thickness,
        ownScale = s.scale,
        rotation = s.rotation,
    }
end

function M.regions(frame)
    return W[frame].regions
end
function M.children(frame)
    return W[frame].children
end

--- `root` and everything below it (frames and regions), parents first;
-- optionally only one widget type and/or only visible (true) or hidden
-- (false) widgets.
function M.descendants(root, widgetType, visibility)
    local out = {}
    local function keep(widget)
        local s = W[widget]
        if widgetType ~= nil and s.type ~= widgetType then
            return
        end
        if visibility ~= nil and isVisible(widget) ~= visibility then
            return
        end
        out[#out + 1] = widget
    end
    local function walk(widget)
        keep(widget)
        local s = W[widget]
        for index = 1, #s.regions do
            keep(s.regions[index])
        end
        for index = 1, #s.children do
            walk(s.children[index])
        end
    end
    walk(root)
    return out
end

--- The visible widgets below `root` that a player can do something with:
-- buttons, sliders, edit boxes and anything with a mouse script.
function M.interactive(root)
    local out = {}
    local kinds = { Button = true, CheckButton = true, Slider = true, EditBox = true }
    for _, widget in ipairs(M.descendants(root, nil, true)) do
        local s = W[widget]
        local scripts = s.scripts
        if kinds[s.type] or scripts.OnEnter or scripts.OnMouseWheel or scripts.OnMouseUp
            or scripts.OnMouseDown or scripts.OnDragStart then
            out[#out + 1] = widget
        end
    end
    return out
end

--- Does the widget have a handler for this script?
function M.script_set(widget, scriptName)
    return W[widget].scripts[scriptName] ~= nil
end

--- Every visible FontString's text below `root` (or anywhere), in creation order.
function M.visible_texts(root)
    local out = {}
    for _, widget in ipairs(allWidgets) do
        local s = W[widget]
        if s.type == "FontString" and s.text and s.text ~= "" and isVisible(widget) then
            local inside = root == nil
            local cursor = widget
            while cursor and not inside do
                if cursor == root then
                    inside = true
                end
                cursor = W[cursor].parent
            end
            if inside then
                out[#out + 1] = s.text
            end
        end
    end
    return out
end

--- Mock methods that this client's widgets do not have (must stay empty).
function M.invalid_implementations()
    local invalid = {}
    for className, methods in pairs(classes) do
        local info = infoOf(className)
        if not info then
            invalid[#invalid + 1] = className .. " (unknown widget type)"
        else
            for methodName in pairs(methods) do
                if not info.methods[methodName] then
                    invalid[#invalid + 1] = className .. ":" .. methodName
                end
            end
        end
    end
    table.sort(invalid)
    return invalid
end

----------------------------------------------------------------------------
-- SavedVariables
----------------------------------------------------------------------------
-- The client writes saved variables as Lua source and reads them back at
-- the next login, so only plain data survives - and a number it cannot
-- write as a literal (nan, inf) makes the whole file unreadable.

local function serialize(value, path, ancestors, out)
    if secretFlavour[value] then
        fail("secret value in SavedVariables at " .. path)
    end
    local valueType = type(value)
    if valueType == "number" then
        if value ~= value or value == huge or value == -huge then
            fail("non-finite number (" .. tostring(value) .. ") in SavedVariables at " .. path
                .. " - the client writes it as text it cannot load back, losing the whole file")
        end
        out[#out + 1] = sformat("%.17g", value)
    elseif valueType == "string" then
        out[#out + 1] = sformat("%q", value)
    elseif valueType == "boolean" then
        out[#out + 1] = tostring(value)
    elseif valueType == "table" then
        if ancestors[value] then
            fail("SavedVariables contain a reference cycle at " .. path)
        end
        ancestors[value] = true
        out[#out + 1] = "{"
        for key, item in pairs(value) do
            local keyType, itemType = type(key), type(item)
            if secretFlavour[key] then
                fail("secret value used as a key in SavedVariables at " .. path)
            end
            local storable = secretFlavour[item] or itemType == "number" or itemType == "string"
                or itemType == "boolean" or itemType == "table"
            if storable and (keyType == "string" or keyType == "number" or keyType == "boolean") then
                if keyType == "string" then
                    out[#out + 1] = "[" .. sformat("%q", key) .. "]="
                else
                    out[#out + 1] = "[" .. tostring(key) .. "]="
                end
                serialize(item, path .. "." .. tostring(key), ancestors, out)
                out[#out + 1] = ","
            end
        end
        out[#out + 1] = "}"
        ancestors[value] = nil
    else
        out[#out + 1] = "nil"
    end
end

--- Lua source for a saved variable, or nil if it is nil.
function M.serialize(name)
    local value = rawget(G, name)
    if value == nil then
        return nil
    end
    local out = {}
    serialize(value, name, {}, out)
    return table.concat(out)
end

function M.deserialize(source)
    local chunk, err = loadstring("return " .. source, "=SavedVariables")
    if not chunk then
        error(err, 0)
    end
    setfenv(chunk, {})
    return chunk()
end

----------------------------------------------------------------------------
-- Secret leak scan
----------------------------------------------------------------------------

--- Paths of every secret value reachable from the addon: its globals, its
-- private table, every widget it created (fields and script handlers) and
-- the upvalues of its own functions.
function M.find_secrets(ns, sourcePrefix)
    local found, seen = {}, {}
    -- Breadth first, so that each secret is reported by its shortest path.
    local queue, head = {}, 1
    local function push(value, path)
        if secretFlavour[value] then
            found[#found + 1] = path
            return
        end
        local valueType = type(value)
        if (valueType == "table" or valueType == "function") and not seen[value] then
            seen[value] = true
            queue[#queue + 1] = value
            queue[#queue + 1] = path
        end
    end

    push(G, "_G")
    push(ns, "ns")
    for index = 1, #allWidgets do
        push(allWidgets[index], "widget#" .. index)
    end

    while head <= #queue do
        local value, path = queue[head], queue[head + 1]
        head = head + 2
        if type(value) == "function" then
            local info = debug.getinfo(value, "S")
            -- Only the addon's own functions: not the mock's, not C.
            if info.what == "Lua" and info.source:sub(1, #sourcePrefix) == sourcePrefix then
                local index = 1
                while true do
                    local name, upvalue = debug.getupvalue(value, index)
                    if not name then
                        break
                    end
                    push(upvalue, path .. "^" .. name)
                    index = index + 1
                end
            end
        else
            local s = W[value]
            if s then
                for scriptName, handler in pairs(s.scripts) do
                    push(handler, path .. "<" .. scriptName .. ">")
                end
            end
            for key, item in next, value do
                if secretFlavour[key] then
                    found[#found + 1] = path .. ".<secret key>"
                end
                push(item, path .. "." .. tostring(key))
            end
        end
    end
    return found
end

return M
