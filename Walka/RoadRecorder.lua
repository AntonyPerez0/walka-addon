-- Walka road recorder: the game side of Core/Road.lua.
--
-- Four times a second it asks the game where the character is and hands
-- that to the recorder, which keeps the points worth keeping. The road is
-- saved per character (WalkaCharDB), so it costs nothing when another
-- character logs in.
--
-- It runs on a timer of its own, apart from the tick that feeds the
-- beacon, and every look is wrapped in pcall: whatever a client build may
-- do to these calls, an error here can never reach the belt control.
--
-- The calls and their order are the ones Road to Sixty's recorder uses in
-- this client (see Core/Road.lua): ask whether we are in an instance
-- first (positions there are nil or cannot be placed), then the best map
-- for the player, the position on it, and the world position from that.

local ADDON_NAME, ns = ...

local Road = ns.Road
local Game = ns.Game
local Journey = ns.Journey

local RoadRecorder = {}
ns.RoadRecorder = RoadRecorder

local floor = math.floor

RoadRecorder.LOOK_SECONDS = 0.25
RoadRecorder.FLUSH_SECONDS = 60     -- the game cannot be relied on to say when it logs out

local core              -- the addon's core (Walka); nil until Load
local db                -- WalkaCharDB, made sound
local recorder
local ticker
local lastLook = 0      -- GetTime() of the last look that ran to its end
local lastFlush = 0
local enabled = true
local named = false     -- the saved road carries this character's "Name-Realm"

local function isSecret(value)
    return issecretvalue ~= nil and issecretvalue(value)
end

local function plainNumber(value)
    if isSecret(value) or type(value) ~= "number" or value ~= value then
        return nil
    end
    return value
end

--- Where the character is: continent id and world yards (whole numbers),
-- or nil if the game cannot say (an instance, a loading screen).
local function whereAmI()
    local inInstance = IsInInstance and IsInInstance()
    if isSecret(inInstance) or inInstance then
        return nil
    end
    local mapId = C_Map and C_Map.GetBestMapForUnit and C_Map.GetBestMapForUnit("player")
    if not plainNumber(mapId) then
        return nil
    end
    local position = C_Map.GetPlayerMapPosition(mapId, "player")
    if isSecret(position) or not position then
        return nil
    end
    local continent, world = C_Map.GetWorldPosFromMapPos(mapId, position)
    if isSecret(world) or not world or not plainNumber(continent) then
        return nil
    end
    local x, y = plainNumber(world.x), plainNumber(world.y)
    if not x or not y then
        return nil
    end
    return continent, floor(x + 0.5), floor(y + 0.5)
end

-- "Name-Realm" of the character, or nil while the game will not say. The
-- companion finds the road of a character by this name (it is what the
-- journey's CHAR_KEY is a hash of), so a "?" must never replace a real one.
local function characterKey()
    local key = Game.CharacterName()
    if Journey.isCharacter(key) then
        return key
    end
    return nil
end

-- How the character is travelling, from the state the core samples.
local function travelMode(state)
    if state.taxi then
        return "t"
    elseif state.dead then
        return "g"
    elseif state.mounted then
        return "r"
    end
    return "w"
end

-- Is the treadmill belt moving, as far as the addon can tell? A session is
-- running, not paused, and the belt estimate is above zero.
local function beltMoving()
    if not core or not core:IsActive() or core:IsPaused() then
        return false
    end
    local belt = core:GetLiveSpeed()
    return type(belt) == "number" and belt > 0
end

local function look()
    if not recorder or not enabled then
        return
    end
    if not named then
        local key = characterKey()      -- (the game may not have said at login)
        if key then
            db.key = key
            named = true
        end
    end
    local now = Game.Now()
    local continent, x, y = whereAmI()
    local state = core:GetGameState()
    local level = plainNumber(UnitLevel and UnitLevel("player")) or 0
    recorder:sample(now, continent, x, y, travelMode(state), beltMoving(), level)
    local clock = GetTime()
    if clock - lastFlush >= RoadRecorder.FLUSH_SECONDS then
        lastFlush = clock
        recorder:flush()
    end
    lastLook = clock
end

local function guardedLook()
    local ok, problem = pcall(look)
    if not ok and not RoadRecorder.problem then
        -- Remembered for /walka mapcheck; not shouted four times a second.
        RoadRecorder.problem = tostring(problem)
    end
end

local function startTicker()
    if ticker and ticker.Cancel then
        ticker:Cancel()
    end
    ticker = C_Timer and C_Timer.NewTicker and C_Timer.NewTicker(RoadRecorder.LOOK_SECONDS, guardedLook) or nil
end

--- Start recording. `walka` is the addon's core, loaded.
function RoadRecorder.Load(walka)
    core = walka
    local guid = Game.CharacterGUID()
    local key = characterKey()
    WalkaCharDB = Road.database(WalkaCharDB, guid, key)
    db = WalkaCharDB
    named = key ~= nil
    recorder = Road.Recorder.new(db)
    enabled = walka.db.settings.recordRoad ~= false
    lastFlush = GetTime()
    startTicker()
end

--- Switch recording on or off (the road so far is kept either way).
function RoadRecorder.SetEnabled(on)
    enabled = on and true or false
    if not enabled and recorder then
        recorder:close(Game.Now())
    end
end

function RoadRecorder.IsEnabled()
    return enabled
end

--- The road database of this character (nil before Load).
function RoadRecorder.GetDatabase()
    return db
end

--- Make sure everything recorded so far is in the saved table (before the
-- map reads it, or the game saves it).
function RoadRecorder.Flush()
    if recorder then
        recorder:flush()
    end
end

--- The end of the road that is not stored yet: from the last stored point
-- of the stretch being recorded to where the character was at the last
-- look. Returns continent, fromX, fromY, toX, toY, mode, belt (true if the
-- treadmill belt is moving), level and the segment being recorded (to
-- tell one stretch from the next) - or nil when no stretch is being
-- recorded (standing in a dungeon, recording switched off).
function RoadRecorder.Tail()
    local segment = recorder and enabled and recorder.segment
    if not segment or not recorder.prevX then
        return nil
    end
    return segment.c, recorder.lastX, recorder.lastY, recorder.prevX, recorder.prevY,
        segment.m, segment.b == 1, segment.l, segment
end

--- Where the character is right now (continent, x, y in world yards), or nil.
function RoadRecorder.WhereAmI()
    local ok, continent, x, y = pcall(whereAmI)
    if ok then
        return continent, x, y
    end
    return nil
end

--- Called by the core on every tick: restarts the timer should it ever
-- have stopped (a ticker that raised is not rescheduled by every client).
function RoadRecorder.Watchdog()
    if recorder and enabled and GetTime() - lastLook > 5 then
        lastLook = GetTime()
        startTicker()
    end
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_ENTERING_WORLD")
events:RegisterEvent("PLAYER_LOGOUT")
events:SetScript("OnEvent", function(_, event)
    if not recorder then
        return
    end
    if event == "PLAYER_ENTERING_WORLD" then
        -- After every loading screen the character may be anywhere: the
        -- road before it and the road after it are not joined by a line.
        recorder:close(Game.Now())
    else
        recorder:close(Game.Now())
        recorder:flush()
    end
end)

return RoadRecorder
