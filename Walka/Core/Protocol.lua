-- Walka beacon protocol: frame packing + CRC.
--
-- Pure Lua 5.1 (no WoW API, no `bit` library) so it runs both inside the game
-- and in the test-suite. The matching decoder lives in the companion app
-- (companion/src/walka/protocol.py). The format is specified in
-- docs/PROTOCOL.md; keep all three in sync.
--
-- A frame is 10 bytes: 8 payload bytes + CRC-16/CCITT-FALSE (big-endian).
-- Payload bit layout, most-significant bit first:
--
--   bits  0..3   version        (4)   = Protocol.VERSION
--   bits  4..5   frame type     (2)   0 = live state
--   bits  6..13  seq            (8)   heartbeat counter, wraps at 256
--   bits 14..21  session        (8)   session counter, 0 = no session
--   bits 22..37  flags          (16)  see Protocol.FLAG
--   bits 38..49  target speed   (12)  belt speed wanted, 0.01 km/h units
--   bits 50..61  character speed(12)  0.02 yd/s units
--   bits 62..63  reserved       (2)   = 0

local ADDON_NAME, ns = ...
ns = ns or {}

local Protocol = {}
ns.Protocol = Protocol

Protocol.VERSION = 1
Protocol.FRAME_BYTES = 10
Protocol.PAYLOAD_BYTES = 8
Protocol.FRAME_BITS = 80

Protocol.TYPE_LIVE = 0

-- Units of the two speed fields.
Protocol.TARGET_UNIT_KMH = 0.01
Protocol.TARGET_MAX_RAW = 4095
Protocol.CHAR_UNIT_YPS = 0.02
Protocol.CHAR_MAX_RAW = 4095

-- Flag bit values (bit 0 is the least-significant bit of the 16-bit field).
Protocol.FLAG = {
    ACTIVE   = 1,     -- a walking session is running: the belt may be driven
    PAUSED   = 2,     -- session paused: the belt must be paused
    MOVING   = 4,     -- character speed > 0
    MOUNTED  = 8,
    TAXI     = 16,    -- on a flight path
    SWIMMING = 32,
    FLYING   = 64,
    DEAD     = 128,   -- dead or ghost
    COMBAT   = 256,
    AFK      = 512,
    FALLING  = 1024,
    RESTING  = 2048,  -- inn / city rest state
    ESTIMATED = 4096, -- character speed is an estimate (the game hides the
                      -- real value from addons during combat)
}

local floor = math.floor

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

--- Pack a frame into `out` (an array reused between calls) as 80 bits,
-- each 0 or 1, most-significant bit first. Returns `out`.
--
-- frame = {
--   seq       = 0..255,
--   session   = 0..255,
--   flags     = 0..65535 (sum of Protocol.FLAG values),
--   target    = raw target belt speed (0.01 km/h units, 0..4095),
--   charSpeed = raw character speed (0.02 yd/s units, 0..4095),
-- }
function Protocol.packBits(frame, out)
    out = out or {}
    local values = {
        version = Protocol.VERSION,
        frameType = frame.frameType or Protocol.TYPE_LIVE,
        seq = (frame.seq or 0) % 256,
        session = (frame.session or 0) % 256,
        flags = (frame.flags or 0) % 65536,
        target = clampInt(frame.target, Protocol.TARGET_MAX_RAW),
        charSpeed = clampInt(frame.charSpeed, Protocol.CHAR_MAX_RAW),
        reserved = 0,
    }

    local n = 0
    for f = 1, #FIELDS do
        local name, width = FIELDS[f][1], FIELDS[f][2]
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

--- Pack a frame and return it as an array of 10 byte values (for tests/tools).
function Protocol.packBytes(frame)
    local bits = Protocol.packBits(frame, {})
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

--- Convert a belt speed in km/h to the raw target field.
function Protocol.kmhToRaw(kmh)
    return clampInt((kmh or 0) / Protocol.TARGET_UNIT_KMH, Protocol.TARGET_MAX_RAW)
end

--- Convert a character speed in yards/second to the raw character-speed field.
function Protocol.ypsToRaw(yps)
    return clampInt((yps or 0) / Protocol.CHAR_UNIT_YPS, Protocol.CHAR_MAX_RAW)
end

return Protocol
