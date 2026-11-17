-- Walka beacon: paints the protocol frame as a strip of black/white squares
-- in the top-left corner of the game window, where the companion app reads it
-- with a screen capture. See docs/PROTOCOL.md.
--
--   cell:   1 2 3 4 | 5 ............ 84 | 85 86
--           W B W B |   80 frame bits   |  W  B
--           preamble   white=1 black=0    postamble
--
-- The strip is drawn in physical pixels: the frame has no parent (so it is
-- unaffected by the UI scale and stays visible when the UI is hidden with
-- Alt+Z) and its own scale is 768 / screenHeight, which makes one UI unit
-- exactly one pixel.

local ADDON_NAME, ns = ...

local Beacon = {}
ns.Beacon = Beacon

local Protocol = ns.Protocol

local PREAMBLE = { 1, 0, 1, 0 }
local POSTAMBLE = { 1, 0 }
local FRAME_BITS = Protocol.FRAME_BITS
local TOTAL_CELLS = #PREAMBLE + FRAME_BITS + #POSTAMBLE

Beacon.TOTAL_CELLS = TOTAL_CELLS
Beacon.DEFAULT_CELL_PX = 4
Beacon.MIN_CELL_PX = 2
Beacon.MAX_CELL_PX = 8

local frame          -- the strip's frame
local backing        -- black quiet zone behind and around the cells
local cells = {}     -- textures, 1..TOTAL_CELLS
local shown = {}     -- last value painted into each cell (0/1)
local bits = {}      -- scratch buffer for Protocol.packBits
local cellPx = Beacon.DEFAULT_CELL_PX

local function paint(index, value)
    if shown[index] ~= value then
        shown[index] = value
        cells[index]:SetColorTexture(value, value, value, 1)
    end
end

local function layout()
    local _, physicalHeight = GetPhysicalScreenSize()
    if not physicalHeight or physicalHeight <= 0 then
        physicalHeight = 768
    end
    -- One UI unit == one physical pixel for this frame.
    frame:SetScale(768 / physicalHeight)
    frame:ClearAllPoints()
    frame:SetPoint("TOPLEFT", 0, 0)
    -- One extra cell of black on the right and below as a quiet zone.
    frame:SetSize((TOTAL_CELLS + 1) * cellPx, cellPx * 2)
    for index = 1, TOTAL_CELLS do
        local cell = cells[index]
        cell:ClearAllPoints()
        cell:SetPoint("TOPLEFT", frame, "TOPLEFT", (index - 1) * cellPx, 0)
        cell:SetSize(cellPx, cellPx)
    end
end

--- Create the strip. Safe to call more than once.
function Beacon.Create()
    if frame then
        return frame
    end
    frame = CreateFrame("Frame", "WalkaBeaconFrame", nil)
    frame:SetFrameStrata("TOOLTIP")
    frame:SetFrameLevel(9999)
    frame:EnableMouse(false)

    backing = frame:CreateTexture(nil, "BACKGROUND")
    backing:SetAllPoints(frame)
    backing:SetColorTexture(0, 0, 0, 1)

    for index = 1, TOTAL_CELLS do
        local cell = frame:CreateTexture(nil, "OVERLAY")
        -- Keep the engine from nudging cells onto its own pixel grid: the
        -- coordinates are already whole pixels.
        if cell.SetSnapToPixelGrid then
            cell:SetSnapToPixelGrid(false)
            cell:SetTexelSnappingBias(0)
        end
        cell:SetColorTexture(0, 0, 0, 1)
        cells[index] = cell
        shown[index] = 0
    end

    -- The guard cells never change.
    for index = 1, #PREAMBLE do
        paint(index, PREAMBLE[index])
    end
    for index = 1, #POSTAMBLE do
        paint(#PREAMBLE + FRAME_BITS + index, POSTAMBLE[index])
    end

    layout()

    frame:RegisterEvent("DISPLAY_SIZE_CHANGED")
    frame:RegisterEvent("UI_SCALE_CHANGED")
    frame:SetScript("OnEvent", layout)
    return frame
end

--- The beacon's frame (nil before Create). Its OnUpdate drives the addon.
function Beacon.GetFrame()
    return frame
end

--- Set the size of one cell in physical pixels (clamped to 2..8).
function Beacon.SetCellSize(pixels)
    pixels = tonumber(pixels)
    -- tonumber("nan") is a number, and NaN slips through every comparison
    -- below; it would reach SetSize and, worse, the saved variables (which
    -- the client then cannot read back).
    if not pixels or pixels ~= pixels then
        pixels = Beacon.DEFAULT_CELL_PX
    end
    pixels = math.floor(pixels + 0.5)
    if pixels < Beacon.MIN_CELL_PX then
        pixels = Beacon.MIN_CELL_PX
    elseif pixels > Beacon.MAX_CELL_PX then
        pixels = Beacon.MAX_CELL_PX
    end
    cellPx = pixels
    if frame then
        layout()
    end
end

function Beacon.GetCellSize()
    return cellPx
end

--- Show or hide the strip. Hidden means the companion sees no signal.
-- The frame itself stays shown so that its OnUpdate keeps running.
function Beacon.SetVisible(visible)
    if not frame then
        return
    end
    frame:SetAlpha(visible and 1 or 0)
end

--- Paint a protocol frame (see Protocol.packBits for the fields).
function Beacon.Send(protocolFrame)
    if not frame then
        return
    end
    Protocol.packBits(protocolFrame, bits)
    local offset = #PREAMBLE
    for index = 1, FRAME_BITS do
        paint(offset + index, bits[index])
    end
end

--- The cell values currently painted (1 = white, 0 = black), for tests.
function Beacon.GetCells()
    local copy = {}
    for index = 1, TOTAL_CELLS do
        copy[index] = shown[index]
    end
    return copy
end

return Beacon
