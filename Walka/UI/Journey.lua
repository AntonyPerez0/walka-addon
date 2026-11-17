-- Walka journey window: one character's journey so far, as a card.
--
--   Walka  journey                                               [x]
--   Tester                                  LEVEL      TIME PLAYED
--   tracked since Nov 03, 2026              17 / 60    21h 25m
--   ----------------------------------------------------------------
--   MY STEPS                        CHARACTER STEPS
--   ~ 105,937                       about 93,210
--   ~53.0 mi walked                 46.6 in-game miles on foot
--   [==================    ]        [===============       ]
--   ----------------------------------------------------------------
--   CALORIES            TIME ON THE BELT         WALKS
--   ~8,200 kcal         31:02:10                 41
--   ----------------------------------------------------------------
--   Totals through Nov 17, 2026.
--   ~ includes estimates the treadmill has not confirmed yet
--   The companion app's Share page turns these numbers
--   into a picture ready to post.                      [Screenshot]
--
-- Left is the player, right is the character; the two bars compare how far
-- each has walked. The numbers come from Tracker.GetSummary() - the same
-- whole numbers the companion app receives for its picture. The window is
-- built the first time it is opened, and is an ordinary (unprotected)
-- frame: it can be opened, moved and closed in combat.

local ADDON_NAME, ns = ...

local Walka = ns.Walka
local Widgets = ns.Widgets
local Tracker = ns.Tracker
local Journey = ns.Journey
local Metrics = ns.Metrics
local COLOR = Widgets.COLOR

local JourneyWindow = {}
ns.JourneyWindow = JourneyWindow

local WIDTH, HEIGHT = 440, 310
local MARGIN = 16
local INNER = WIDTH - 2 * MARGIN

local COLUMN_GAP = 16
local COLUMN_WIDTH = (INNER - COLUMN_GAP) / 2
local BAR_HEIGHT = 6

-- Where the window opens until the player drags it.
local DEFAULT_POINT = { "CENTER", "CENTER", 0, 40 }

local frame         -- the window; nil until first opened
local parts = {}

-- What is shown before the game has said which character this is.
local NOBODY = {
    name = "Your character",
    level = 0, maxLevel = 0,
    footYd = 0, travelYd = 0, characterSteps = 0,
    steps = 0, distanceM = 0, kcal = 0, seconds = 0, walks = 0,
    estimated = false,
}

----------------------------------------------------------------------------
-- Formatting
----------------------------------------------------------------------------

-- "21h 25m", "25m"
local function playedText(seconds)
    local minutes = math.floor(seconds / 60)
    local hours = math.floor(minutes / 60)
    if hours > 0 then
        return string.format("%sh %02dm", Walka:FormatNumber(hours), minutes % 60)
    end
    return string.format("%dm", minutes)
end

-- "Nov 17, 2026", or nil for a time the game cannot show.
local function dayText(epoch)
    if type(epoch) ~= "number" then
        return nil
    end
    return date("%b %d, %Y", epoch)
end

-- 53.04 -> "53.0", 2136.52 -> "2,136.5": a journey is long, one decimal
-- is plenty.
local function oneDecimal(value)
    local tenths = math.floor(value * 10 + 0.5)
    return Walka:FormatNumber(math.floor(tenths / 10)) .. "." .. (tenths % 10)
end

-- "53.0 mi" / "85.3 km"
local function realDistance(meters)
    if Walka:IsMph() then
        return oneDecimal(meters / Metrics.METERS_PER_MILE) .. " mi"
    end
    return oneDecimal(meters / 1000) .. " km"
end

-- "46.6 in-game miles" / "68.5 in-game km"
local function gameDistance(yards)
    if Walka:IsMph() then
        return oneDecimal(yards / Journey.YARDS_PER_MILE) .. " in-game miles"
    end
    return oneDecimal(yards * Journey.METERS_PER_YARD / 1000) .. " in-game km"
end

----------------------------------------------------------------------------
-- Pieces
----------------------------------------------------------------------------

-- A caption in Walka's blue with a value under it. The block takes the
-- mouse, so that it can have a tooltip.
local function createTile(parent, caption, width, valueFont)
    local tile = CreateFrame("Frame", nil, parent)
    tile:SetSize(width, 34)
    tile:EnableMouse(true)
    tile.Caption = Widgets.Text(tile, "GameFontNormalSmall", caption, "LEFT")
    tile.Caption:SetPoint("TOPLEFT", tile, "TOPLEFT", 0, 0)
    tile.Caption:SetTextColor(COLOR.accent[1], COLOR.accent[2], COLOR.accent[3])
    tile.Value = Widgets.Text(tile, valueFont or "GameFontHighlight", "", "LEFT")
    tile.Value:SetPoint("TOPLEFT", tile, "TOPLEFT", 0, -14)
    return tile
end

-- One of the two big columns: caption, a large number with a small word in
-- front of it ("~", "about"), a line of detail and a bar. The word has a
-- slot of its own width, so the number does not jump when it appears.
local function createColumn(parent, caption, markWidth, barColor)
    local column = CreateFrame("Frame", nil, parent)
    column:SetSize(COLUMN_WIDTH, 80)
    column:EnableMouse(true)

    column.Caption = Widgets.Text(column, "GameFontNormalSmall", caption, "LEFT")
    column.Caption:SetPoint("TOPLEFT", column, "TOPLEFT", 0, 0)
    column.Caption:SetTextColor(COLOR.accent[1], COLOR.accent[2], COLOR.accent[3])

    column.Mark = Widgets.Text(column, "GameFontNormalSmall", "", "LEFT")
    column.Mark:SetPoint("BOTTOMLEFT", column, "TOPLEFT", 0, -42)
    column.Mark:SetWidth(markWidth)
    column.Mark:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    column.Value = Widgets.Text(column, "NumberFontNormalHuge", "0", "LEFT")
    column.Value:SetPoint("BOTTOMLEFT", column, "TOPLEFT", markWidth + 2, -46)

    column.Detail = Widgets.Text(column, "GameFontHighlightSmall", "", "LEFT")
    column.Detail:SetPoint("TOPLEFT", column, "TOPLEFT", 0, -52)

    column.BarBack = Widgets.Fill(column, "BORDER", 1, 1, 1, 0.10)
    column.BarBack:SetSize(COLUMN_WIDTH, BAR_HEIGHT)
    column.BarBack:SetPoint("BOTTOMLEFT", column, "BOTTOMLEFT", 0, 0)
    column.Bar = Widgets.Fill(column, "ARTWORK", barColor[1], barColor[2], barColor[3], 0.9)
    column.Bar:SetSize(1, BAR_HEIGHT)
    column.Bar:SetPoint("BOTTOMLEFT", column, "BOTTOMLEFT", 0, 0)
    column.Bar:Hide()
    return column
end

local function createRule(parent, y)
    local rule = Widgets.Fill(parent, "ARTWORK", 1, 1, 1, 0.08)
    rule:SetHeight(1)
    rule:SetPoint("TOPLEFT", parent, "TOPLEFT", MARGIN, y)
    rule:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -MARGIN, y)
    return rule
end

-- Length of a bar for `value` when the longest bar stands for `longest`.
local function showBar(column, value, longest)
    if value > 0 and longest > 0 then
        column.Bar:SetWidth(math.max(2, COLUMN_WIDTH * value / longest))
        column.Bar:Show()
    else
        column.Bar:Hide()
    end
end

----------------------------------------------------------------------------
-- Redraw
----------------------------------------------------------------------------

local function refresh()
    if not frame or not frame:IsShown() then
        return
    end
    local journey = Tracker.GetSummary() or NOBODY
    local mark = journey.estimated and "~" or ""

    -- Who, since when.
    parts.name:SetText(journey.name)
    local since = dayText(journey.since)
    parts.since:SetText(since and ("tracked since " .. since) or "")

    -- Level and time played.
    if journey.level > 0 then
        parts.level.Value:SetText(journey.level .. Widgets.Colorize(" / " .. journey.maxLevel, COLOR.grey))
    else
        parts.level.Value:SetText(Widgets.Colorize("-", COLOR.grey))
    end
    if journey.played then
        parts.played.Value:SetText(playedText(journey.played))
    else
        parts.played.Value:SetText(Widgets.Colorize("not known yet", COLOR.grey))
    end

    -- The player on the left, the character on the right.
    parts.mine.Mark:SetText(mark)
    parts.mine.Value:SetText(Walka:FormatNumber(journey.steps))
    parts.mine.Detail:SetText(mark .. realDistance(journey.distanceM) .. " walked")
    parts.theirs.Mark:SetText("about")
    parts.theirs.Value:SetText(Walka:FormatNumber(journey.characterSteps))
    parts.theirs.Detail:SetText(gameDistance(journey.footYd) .. " on foot")

    -- The bars: both distances on one scale.
    local walkedM = journey.distanceM
    local ranM = journey.footYd * Journey.METERS_PER_YARD
    local longest = math.max(walkedM, ranM)
    showBar(parts.mine, walkedM, longest)
    showBar(parts.theirs, ranM, longest)

    parts.kcal.Value:SetText(mark .. Walka:FormatNumber(journey.kcal) .. " kcal")
    parts.belt.Value:SetText(mark .. Walka:FormatDuration(journey.seconds))
    parts.walks.Value:SetText(Walka:FormatNumber(journey.walks))

    local today = dayText(ns.Game.Now())
    parts.through:SetText(today and ("Totals through " .. today .. ".") or "")
    parts.legend:SetShown(journey.estimated)
end

----------------------------------------------------------------------------
-- Tooltips
----------------------------------------------------------------------------

local function playedTooltip()
    local journey = Tracker.GetSummary()
    if journey and journey.played then
        return {
            "The game's own /played figure for this character.",
            Widgets.Colorize(
                "Walka asks the game for it once per login, when this window opens, and counts on from there.",
                COLOR.grey
            ),
        }
    end
    return {
        "The game has not said yet.",
        Widgets.Colorize(
            "Walka asks when this window opens - the game prints its answer in the chat as well. "
                .. "Typing /played does the same.",
            COLOR.grey
        ),
    }
end

local function mineTooltip()
    local journey = Tracker.GetSummary()
    if not journey then
        return nil
    end
    local walks = journey.walks == 1 and "1 walk" or (Walka:FormatNumber(journey.walks) .. " walks")
    local lines = {
        "The steps you walked on the treadmill while playing " .. journey.name .. ": " .. walks
            .. ", the one in progress included.",
    }
    if journey.estimated then
        lines[#lines + 1] = Widgets.Colorize(
            "~ Part of it is the addon's estimate. The treadmill's measured numbers replace it once the "
                .. "companion app has synced and you /reload.",
            COLOR.grey
        )
    else
        lines[#lines + 1] = Widgets.Colorize("All of it measured by the treadmill.", COLOR.green)
    end
    return lines
end

local function theirsTooltip()
    local journey = Tracker.GetSummary()
    if not journey then
        return nil
    end
    local since = dayText(journey.since)
    return {
        "How far " .. journey.name .. " has gone on foot" .. (since and (" since " .. since) or "")
            .. ", counted as 2,000 steps to the in-game mile (1,760 yards).",
        Widgets.Colorize("The game does not count steps: this is Walka's own rule of thumb.", COLOR.grey),
        "",
        "Mounts, flight paths, flying and swimming are left out. With them: "
            .. gameDistance(journey.travelYd) .. ".",
    }
end

----------------------------------------------------------------------------
-- Position
----------------------------------------------------------------------------
-- Remembered in the settings as Widgets.Placement describes it.

local function applyPosition()
    if frame.walkaMoving then
        return
    end
    Widgets.Place(frame, Walka:Get("journey.point"), DEFAULT_POINT)
end

local function savePosition()
    local point = Widgets.Placement(frame)
    if point then
        -- Fires "SETTINGS", which re-anchors the frame to the saved point.
        Walka:Set("journey.point", point)
    end
end

----------------------------------------------------------------------------
-- Construction
----------------------------------------------------------------------------

local function create()
    if frame then
        return
    end
    frame = Widgets.Panel(UIParent, "WalkaJourneyFrame", 0.94)
    frame:SetSize(WIDTH, HEIGHT)
    frame:SetFrameStrata("HIGH")
    frame:SetToplevel(true)
    frame:Hide()
    Widgets.MakeMovable(frame, nil, savePosition)
    applyPosition()

    -- Escape closes it, like the game's own windows.
    if type(UISpecialFrames) == "table" then
        table.insert(UISpecialFrames, "WalkaJourneyFrame")
    end

    local title = Widgets.Text(frame, "GameFontNormalLarge", "Walka", "LEFT")
    title:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN + 2, -11)
    local subtitle = Widgets.Text(frame, "GameFontHighlight", "journey", "LEFT")
    subtitle:SetPoint("BOTTOMLEFT", title, "BOTTOMRIGHT", 6, 1)
    subtitle:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    local close = Widgets.CloseButton(frame, function()
        frame:Hide()
    end)
    close:ClearAllPoints()
    close:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -3, -3)

    -- Who.
    parts.name = Widgets.Text(frame, "GameFontHighlightLarge", "", "LEFT")
    parts.name:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -40)
    parts.since = Widgets.Text(frame, "GameFontDisableSmall", "", "LEFT")
    parts.since:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -60)

    -- Level and time played, to the right of the name.
    parts.played = createTile(frame, "TIME PLAYED", 110, "GameFontHighlightLarge")
    parts.played:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -MARGIN, -40)
    Widgets.Tooltip(parts.played, "Time played", playedTooltip, "ANCHOR_BOTTOMRIGHT")
    parts.level = createTile(frame, "LEVEL", 76, "GameFontHighlightLarge")
    parts.level:SetPoint("TOPRIGHT", parts.played, "TOPLEFT", -12, 0)

    createRule(frame, -84)

    -- The two walkers.
    parts.mine = createColumn(frame, "MY STEPS", 10, COLOR.accent)
    parts.mine:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -94)
    Widgets.Tooltip(parts.mine, "My steps", mineTooltip, "ANCHOR_BOTTOMRIGHT")
    parts.theirs = createColumn(frame, "CHARACTER STEPS", 38, COLOR.gold)
    parts.theirs:SetPoint("TOPLEFT", parts.mine, "TOPRIGHT", COLUMN_GAP, 0)
    Widgets.Tooltip(parts.theirs, "Character steps", theirsTooltip, "ANCHOR_BOTTOMRIGHT")

    createRule(frame, -184)

    -- The rest of the real-world numbers.
    local third = INNER / 3
    parts.kcal = createTile(frame, "CALORIES", third)
    parts.kcal:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -194)
    parts.belt = createTile(frame, "TIME ON THE BELT", third)
    parts.belt:SetPoint("TOPLEFT", parts.kcal, "TOPRIGHT", 0, 0)
    parts.walks = createTile(frame, "WALKS", third)
    parts.walks:SetPoint("TOPLEFT", parts.belt, "TOPRIGHT", 0, 0)

    createRule(frame, -234)

    -- Footer.
    parts.through = Widgets.Text(frame, "GameFontHighlightSmall", "", "LEFT")
    parts.through:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -243)
    parts.legend = Widgets.Text(
        frame,
        "GameFontDisableSmall",
        "~  includes estimates the treadmill has not confirmed yet",
        "LEFT"
    )
    parts.legend:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, -257)
    parts.legend:Hide()

    -- The picture for sharing is made outside the game: an addon cannot
    -- write a file. (The line is broken by hand so that it clears the
    -- button whatever the font.)
    parts.share = Widgets.Text(
        frame,
        "GameFontNormalSmall",
        "The companion app's Share page turns these numbers\ninto a picture ready to post.",
        "LEFT"
    )
    parts.share:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", MARGIN, 12)

    -- The game's own screenshot: the whole screen, this window included.
    if Screenshot then
        parts.screenshot = Widgets.Button(frame, "Screenshot", 96, function(self)
            -- (Not with its own tooltip in the picture.)
            Widgets.HideTooltip(self)
            Screenshot()
        end)
        parts.screenshot:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -MARGIN, 12)
        Widgets.Tooltip(
            parts.screenshot,
            "Screenshot",
            "Saves a picture of the whole screen, this window included, in the Screenshots folder of "
                .. "your World of Warcraft installation.",
            "ANCHOR_TOPRIGHT"
        )
    end

    Widgets.Hook(frame, "OnShow", function(self)
        self:Raise()
        -- The game says the time played only when asked; see Tracker.lua.
        Tracker.RequestPlayed()
        refresh()
    end)
end

----------------------------------------------------------------------------
-- Public API
----------------------------------------------------------------------------

function JourneyWindow.Show()
    if not Walka.loaded then
        return
    end
    create()
    if not frame:IsShown() then
        if PlaySound and SOUNDKIT and SOUNDKIT.IG_CHARACTER_INFO_OPEN then
            PlaySound(SOUNDKIT.IG_CHARACTER_INFO_OPEN)
        end
        frame:Show()
    end
end

function JourneyWindow.Hide()
    if frame and frame:IsShown() then
        if PlaySound and SOUNDKIT and SOUNDKIT.IG_CHARACTER_INFO_CLOSE then
            PlaySound(SOUNDKIT.IG_CHARACTER_INFO_CLOSE)
        end
        frame:Hide()
    end
end

function JourneyWindow.Toggle()
    if frame and frame:IsShown() then
        JourneyWindow.Hide()
    else
        JourneyWindow.Show()
    end
end

----------------------------------------------------------------------------
-- Wiring
----------------------------------------------------------------------------

-- The numbers move while the character does: redraw with the other windows.
Walka:On("TICK", refresh)

-- The display unit, or the window's own saved position.
Walka:On("SETTINGS", function()
    if frame then
        applyPosition()
        refresh()
    end
end)

return JourneyWindow
