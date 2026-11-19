-- Walka beacon protocol: frame packing + CRC.
--
-- Pure Lua 5.1 (no WoW API, no `bit` library) so it runs both inside the game
-- and in the test-suite. The matching decoder lives in the companion app
-- (companion/src/walka/protocol.py). The format is specified in
-- docs/PROTOCOL.md; keep all three in sync.
--
-- A frame is 10 bytes: 8 payload bytes + CRC-16/CCITT-FALSE (big-endian).
-- There are two kinds, told apart by the frame type.
--
-- The live frame (Protocol.packBits) says whether a walking session is
-- running and how the character is moving. Payload bit layout,
-- most-significant bit first:
--
--   bits  0..3   version        (4)   = Protocol.VERSION
--   bits  4..5   frame type     (2)   = Protocol.TYPE_LIVE
--   bits  6..13  seq            (8)   heartbeat counter, wraps at 256
--   bits 14..21  session        (8)   session counter, 0 = no session
--   bits 22..37  flags          (16)  see Protocol.FLAG
--   bits 38..49  target speed   (12)  = 0: not used since 0.7 (until then the
--                                     belt speed the addon asked for)
--   bits 50..61  character speed(12)  0.02 yd/s units
--   bits 62..63  reserved       (2)   = 0
--
-- The stat frame (Protocol.packStatBits) carries one number of the
-- character's journey - level, time played, distance on foot, ... - for the
-- picture the companion's dashboard makes:
--
--   bits  0..3   version        (4)   = Protocol.VERSION
--   bits  4..5   frame type     (2)   = Protocol.TYPE_STAT
--   bits  6..13  seq            (8)   the same counter as in live frames
--   bits 14..19  stat id        (6)   see Protocol.STAT
--   bits 20..59  value          (40)  unsigned
--   bits 60..63  reserved       (4)   = 0
--
-- The companion never reads a stat frame as a live one, so while a stat
-- frame is on screen it simply goes without news of the session: send them
-- sparingly (Protocol.STAT_EVERY) and never two in a row.

local ADDON_NAME, ns = ...
ns = ns or {}

local Protocol = {}
ns.Protocol = Protocol

Protocol.VERSION = 1
Protocol.FRAME_BYTES = 10
Protocol.PAYLOAD_BYTES = 8
Protocol.FRAME_BITS = 80

Protocol.TYPE_LIVE = 0
Protocol.TYPE_STAT = 1

-- Units of the two speed fields.
Protocol.TARGET_UNIT_KMH = 0.01
Protocol.TARGET_MAX_RAW = 4095
Protocol.CHAR_UNIT_YPS = 0.02
Protocol.CHAR_MAX_RAW = 4095

-- Flag bit values (bit 0 is the least-significant bit of the 16-bit field).
Protocol.FLAG = {
    ACTIVE   = 1,     -- a walking session is running: the companion files
                      -- the walk it records under this session
    PAUSED   = 2,     -- the session is paused
    MOVING   = 4,     -- character speed > 0
    MOUNTED  = 8,
    TAXI     = 16,    -- on a flight path
    SWIMMING = 32,
    FLYING   = 64,
    -- (128 and 256 are not used: see below.)
    AFK      = 512,
    -- (1024 and 2048 are not used. Until version 0.5 the four said that the
    -- character was dead, in combat, falling or resting. The companion never
    -- acted on any of them, so they are no longer sent: what the companion
    -- does not need stays in the game.)
    ESTIMATED = 4096, -- character speed is an estimate (the game hides the
                      -- real value from addons during combat)
}

-- What the value of a stat frame is (the 6-bit stat id). The companion
-- ignores ids it does not know, so new ones can be added; 0 is never sent.
Protocol.STAT = {
    CHAR_KEY   = 1,   -- Protocol.djb2("Name-Realm"): tells characters apart
    CHAR_INFO  = 2,   -- level, level cap, class, faction: Protocol.charInfo
    PLAYED_S   = 3,   -- total /played seconds; 0 = not known yet
    FOOT_YD    = 4,   -- yards travelled on foot since tracking began
    TRAVEL_YD  = 5,   -- yards travelled by any means
    REAL_STEPS = 6,   -- real-world steps walked while playing this character
    REAL_M     = 7,   -- real-world metres walked
    REAL_KCAL  = 8,   -- kilocalories (gross, whole number)
    REAL_S     = 9,   -- seconds on the moving belt
    WALKS      = 10,  -- number of walks counted
    SINCE      = 11,  -- unix time (seconds) when tracking began
    INFO_FLAGS = 12,  -- sum of Protocol.INFO_FLAG values
    NAME_0     = 16,  -- the character's name, 5 bytes per frame:
    NAME_1     = 17,  --   Protocol.nameValues
    NAME_2     = 18,
    NAME_3     = 19,
}
Protocol.STAT_MAX_ID = 63
Protocol.STAT_MAX_VALUE = 1099511627775  -- 2^40 - 1

-- The order the stats are sent in, over and over. CHAR_KEY comes first: the
-- companion files whatever follows it under that character.
do
    local S = Protocol.STAT
    Protocol.STAT_CYCLE = {
        S.CHAR_KEY, S.NAME_0, S.NAME_1, S.NAME_2, S.NAME_3, S.CHAR_INFO,
        S.PLAYED_S, S.FOOT_YD, S.TRAVEL_YD,
        S.REAL_STEPS, S.REAL_M, S.REAL_KCAL, S.REAL_S, S.WALKS,
        S.SINCE, S.INFO_FLAGS,
    }
end
-- Every STAT_EVERY-th redraw is a stat frame instead of a live frame: at ten
-- redraws a second that is two stats a second, a whole cycle in 8 seconds,
-- and four redraws out of five still say how the session stands.
Protocol.STAT_EVERY = 5

-- Bits of the INFO_FLAGS value.
Protocol.INFO_FLAG = {
    ESTIMATED = 1,  -- the real-world totals include estimates the companion
                    -- has not confirmed yet
}

-- The faction byte of the CHAR_INFO value.
Protocol.FACTION = {
    UNKNOWN  = 0,
    ALLIANCE = 1,
    HORDE    = 2,
}

-- A name travels as its first NAME_BYTES bytes of UTF-8.
Protocol.NAME_BYTES = 20
Protocol.NAME_CHUNK_BYTES = 5

local floor = math.floor
local strbyte = string.byte

--- CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no xorout)
-- over a list of byte values. Check value: crc16({0x31..0x39}) == 0x29B1.
function Protocol.crc16(bytes, count)
    local crc = 0xFFFF
    for i = 1, count or #bytes do
        local b = bytes[i]
        -- crc = crc XOR (b << 8), done arithmetically on the high byte.
        local hi = floor(crc / 256)
        local lo = crc % 256
        local x = 0
        local a, c = hi, b
        local p = 1
        for _ = 1, 8 do
            local abit, cbit = a % 2, c % 2
            if abit ~= cbit then
                x = x + p
            end
            a = (a - abit) / 2
            c = (c - cbit) / 2
            p = p * 2
        end
        crc = x * 256 + lo
        for _ = 1, 8 do
            if crc >= 0x8000 then
                -- (crc << 1) XOR 0x1021, truncated to 16 bits.
                crc = (crc - 0x8000) * 2
                -- XOR with 0x1021 = bits 12, 5 and 0.
                local b12 = floor(crc / 4096) % 2
                local b5 = floor(crc / 32) % 2
                local b0 = crc % 2
                crc = crc + (b12 == 0 and 4096 or -4096)
                crc = crc + (b5 == 0 and 32 or -32)
                crc = crc + (b0 == 0 and 1 or -1)
            else
                crc = crc * 2
            end
        end
    end
    return crc
end

local function clampInt(value, maxValue)
    value = floor((tonumber(value) or 0) + 0.5)
    if value < 0 then
        return 0
    elseif value > maxValue then
        return maxValue
    end
    return value
end

--- Like clampInt, for values that come from saved data and arithmetic the
-- packer cannot vouch for: NaN and non-numbers become 0 instead of
-- spreading into the bits (a NaN "bit" would end up in SetColorTexture).
local function clampWhole(value, maxValue)
    value = tonumber(value)
    if not value or value ~= value or value <= 0 then
        return 0
    elseif value >= maxValue then
        return maxValue
    end
    return floor(value + 0.5)
end

-- Field widths in transmission order.
local FIELDS = {
    { "version", 4 },
    { "frameType", 2 },
    { "seq", 8 },
    { "session", 8 },
    { "flags", 16 },
    { "target", 12 },
    { "charSpeed", 12 },
    { "reserved", 2 },
}
Protocol.FIELDS = FIELDS

local STAT_FIELDS = {
    { "version", 4 },
    { "frameType", 2 },
    { "seq", 8 },
    { "statId", 6 },
    { "value", 40 },
    { "reserved", 4 },
}
Protocol.STAT_FIELDS = STAT_FIELDS

-- Field values of the frame being packed (see packBits, packStatBits).
local scratchValues = {}
local scratchStatValues = {}

--- Write field values as bits into `out`, followed by the CRC of the
-- payload: 80 bits, each 0 or 1, most-significant bit first. Returns `out`.
-- Pure arithmetic on doubles, which hold whole numbers exactly up to 2^53 -
-- far beyond the widest field (40 bits).
local function writeBits(fields, values, out)
    local n = 0
    for f = 1, #fields do
        local name, width = fields[f][1], fields[f][2]
        local value = floor(values[name])
        local base = n
        for i = width, 1, -1 do
            local bitValue = value % 2
            out[base + i] = bitValue
            value = (value - bitValue) / 2
        end
        n = n + width
    end

    -- Payload bytes -> CRC.
    local bytes = Protocol._scratchBytes
    for byteIndex = 1, Protocol.PAYLOAD_BYTES do
        local v = 0
        local base = (byteIndex - 1) * 8
        for i = 1, 8 do
            v = v * 2 + out[base + i]
        end
        bytes[byteIndex] = v
    end
    local crc = Protocol.crc16(bytes, Protocol.PAYLOAD_BYTES)
    for i = 16, 1, -1 do
        local bitValue = crc % 2
        out[n + i] = bitValue
        crc = (crc - bitValue) / 2
    end
    return out
end
Protocol._scratchBytes = {}

--- The 10 byte values of 80 bits (for tests/tools).
local function bitsToBytes(bits)
    local bytes = {}
    for byteIndex = 1, Protocol.FRAME_BYTES do
        local v = 0
        local base = (byteIndex - 1) * 8
        for i = 1, 8 do
            v = v * 2 + bits[base + i]
        end
        bytes[byteIndex] = v
    end
    return bytes
end

--- Pack a live frame into `out` (an array reused between calls) as 80 bits,
-- each 0 or 1, most-significant bit first. Returns `out`.
--
-- frame = {
--   seq       = 0..255,
--   session   = 0..255,
--   flags     = 0..65535 (sum of Protocol.FLAG values),
--   target    = 0 (the field is not used since 0.7; 0.01 km/h units, 0..4095),
--   charSpeed = raw character speed (0.02 yd/s units, 0..4095),
-- }
function Protocol.packBits(frame, out)
    out = out or {}
    -- (One table for every call: this runs ten times a second in the game,
    -- and a fresh table each time would only be garbage to collect.)
    local values = scratchValues
    values.version = Protocol.VERSION
    -- Always: these fields under another type would be read as something
    -- else entirely (as a stat frame, for one).
    values.frameType = Protocol.TYPE_LIVE
    values.seq = (frame.seq or 0) % 256
    values.session = (frame.session or 0) % 256
    values.flags = (frame.flags or 0) % 65536
    values.target = clampInt(frame.target, Protocol.TARGET_MAX_RAW)
    values.charSpeed = clampInt(frame.charSpeed, Protocol.CHAR_MAX_RAW)
    values.reserved = 0
    return writeBits(FIELDS, values, out)
end

--- Pack a live frame and return it as an array of 10 byte values (for
-- tests/tools).
function Protocol.packBytes(frame)
    return bitsToBytes(Protocol.packBits(frame, {}))
end

--- Pack a stat frame into `out` (an array reused between calls) as 80 bits,
-- each 0 or 1, most-significant bit first. Returns `out`.
--
--   seq    = the redraw counter shared with live frames (wraps at 256)
--   statId = one of Protocol.STAT
--   value  = a whole number, 0..Protocol.STAT_MAX_VALUE
--
-- Whatever goes in, the frame stays valid: the value is rounded and clamped
-- to the field (nil, NaN and negative numbers become 0), and a stat id that
-- is not a whole number 0..63 is sent as 0, which the companion ignores.
function Protocol.packStatBits(seq, statId, value, out)
    out = out or {}
    statId = tonumber(statId)
    if not statId or statId ~= floor(statId) or statId < 0 or statId > Protocol.STAT_MAX_ID then
        statId = 0
    end
    seq = tonumber(seq)
    if not seq or seq ~= seq or seq == math.huge or seq == -math.huge then
        seq = 0
    end
    local values = scratchStatValues
    values.version = Protocol.VERSION
    values.frameType = Protocol.TYPE_STAT
    values.seq = floor(seq) % 256
    values.statId = statId
    values.value = clampWhole(value, Protocol.STAT_MAX_VALUE)
    values.reserved = 0
    return writeBits(STAT_FIELDS, values, out)
end

--- Pack a stat frame and return it as an array of 10 byte values (for
-- tests/tools).
function Protocol.packStatBytes(seq, statId, value)
    return bitsToBytes(Protocol.packStatBits(seq, statId, value, {}))
end

-- The helpers below build the values of some stats. Like everything in this
-- file they are plain Lua and look at what they are given: hand them only
-- values that are safe to look at (Game.lua explains which are not).

--- The value of STAT.CHAR_KEY: the djb2 hash of a string's bytes, 32 bits
-- (h = 5381, then h = (h * 33 + byte) mod 2^32 for every byte). Hash the
-- character's "Name-Realm": the companion can tell characters apart and the
-- realm never has to be drawn on screen.
function Protocol.djb2(text)
    text = tostring(text or "")
    local h = 5381
    for i = 1, #text do
        -- (h * 33 + 255 stays below 2^38: exact in a double.)
        h = (h * 33 + strbyte(text, i)) % 4294967296
    end
    return h
end

--- The value of STAT.CHAR_INFO:
-- level | maxLevel << 8 | classId << 16 | faction << 24, one byte each
-- (faction: one of Protocol.FACTION). Anything unknown may be nil: 0.
function Protocol.charInfo(level, maxLevel, classId, faction)
    return clampWhole(level, 255)
        + clampWhole(maxLevel, 255) * 256
        + clampWhole(classId, 255) * 65536
        + clampWhole(faction, 255) * 16777216
end

--- The values of STAT.NAME_0 .. NAME_3 for a character's name (without the
-- realm), written into `out[1..4]` (a new table if nil). Returns `out`.
--
-- The name's UTF-8 bytes, at most NAME_BYTES of them, padded with zeros;
-- five bytes per value, the first byte in the most significant position. A
-- longer name is cut between two characters, never inside one.
function Protocol.nameValues(name, out)
    out = out or {}
    if type(name) ~= "string" then
        name = ""
    end
    local length = #name
    if length > Protocol.NAME_BYTES then
        length = Protocol.NAME_BYTES
        -- Bytes 128..191 continue the character that began before them: as
        -- long as the first byte left out is one of those, the cut is
        -- inside a character.
        while length > 0 do
            local nextByte = strbyte(name, length + 1)
            if nextByte < 128 or nextByte > 191 then
                break
            end
            length = length - 1
        end
    end
    local chunkBytes = Protocol.NAME_CHUNK_BYTES
    for chunk = 1, Protocol.NAME_BYTES / chunkBytes do
        local value = 0
        for i = (chunk - 1) * chunkBytes + 1, chunk * chunkBytes do
            value = value * 256 + (i <= length and strbyte(name, i) or 0)
        end
        out[chunk] = value
    end
    return out
end

--- Convert a belt speed in km/h to the raw target field. (The addon no
-- longer sends one; kept so that the field can be written and checked.)
function Protocol.kmhToRaw(kmh)
    return clampInt((kmh or 0) / Protocol.TARGET_UNIT_KMH, Protocol.TARGET_MAX_RAW)
end

--- Convert a character speed in yards/second to the raw character-speed field.
function Protocol.ypsToRaw(yps)
    return clampInt((yps or 0) / Protocol.CHAR_UNIT_YPS, Protocol.CHAR_MAX_RAW)
end

return Protocol
