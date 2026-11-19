-- Walka history window: totals, the last seven days and the list of
-- sessions.
--
--   [ Today ]  [ Last 7 days ]  [ All time ]      three summary cards
--   Streak ...                  Session bests ...
--   |  |  |  |  |  |  |                            steps per day
--   When   Time   Distance   Steps   kcal   Avg speed   Character
--   ... eight rows, mouse wheel or scroll bar ...
--   Your characters ran 12.40 mi while you walked 1.30 mi.
--
-- The list is a fixed pool of row frames that is refilled when it scrolls,
-- so it costs the same for 10 sessions as for 400. The window is built the
-- first time it is opened.

local ADDON_NAME, ns = ...

local Walka = ns.Walka
local Widgets = ns.Widgets
local COLOR = Widgets.COLOR

local History = {}
ns.History = History

local WIDTH, HEIGHT = 560, 440
local MARGIN = 14
local INNER = WIDTH - 2 * MARGIN

local CARD_WIDTH, CARD_HEIGHT, CARD_GAP = 172, 84, 8
local CARDS_TOP = -34

local CHART_TOP = -146
local CHART_HEIGHT = 82
local CHART_LABEL_HEIGHT = 14   -- weekday names under the bars
local CHART_BAR_MAX = 50
local CHART_BAR_WIDTH = 38
local CHART_DAYS = 7
local CHART_GUTTER = 34         -- right of the bars: room for the "goal" label

local LIST_TOP = -234
local ROWS = 8
local ROW_HEIGHT = 17
local LIST_WIDTH = INNER - 12   -- room for the scroll bar

local METERS_PER_YARD = ns.Metrics and ns.Metrics.METERS_PER_YARD or 0.9144

-- While a session runs the totals change all the time; redraw them this
-- often. (That is a pass over the whole list, so not on every tick.)
local LIVE_REFRESH_SECONDS = 10
-- Without a session they only change at midnight, when "today" moves on.
local IDLE_REFRESH_SECONDS = 60

-- x and width in the list's own units; they add up to LIST_WIDTH.
local COLUMNS = {
    { key = "when", title = "When", x = 18, width = 88, justify = "LEFT" },
    { key = "time", title = "Time", x = 108, width = 54, justify = "RIGHT" },
    { key = "distance", title = "Distance", x = 164, width = 68, justify = "RIGHT" },
    { key = "steps", title = "Steps", x = 234, width = 56, justify = "RIGHT" },
    { key = "kcal", title = "kcal", x = 292, width = 44, justify = "RIGHT" },
    { key = "speed", title = "Avg speed", x = 338, width = 70, justify = "RIGHT" },
    { key = "who", title = "Character", x = 422, width = 98, justify = "LEFT" },
}

local frame               -- the window; nil until first opened
local parts = {}
local cards = {}          -- today, week, all
local slots = {}          -- chart: one per day
local rows = {}           -- list: ROWS row frames
local sessions = {}       -- Walka:GetHistory() at the last refresh
local offset = 0          -- number of sessions scrolled past
local lastTotalsAt = 0
local stale = false       -- something changed; redraw on the next UI tick

----------------------------------------------------------------------------
-- Formatting
----------------------------------------------------------------------------

local function plural(count, one, many)
    if count == 1 then
        return "1 " .. one
    end
    return Walka:FormatNumber(count) .. " " .. many
end

-- Epoch of noon on a "YYYY-MM-DD" day key (noon keeps clear of DST edges).
local function dayEpoch(key)
    local year, month, day = string.match(key or "", "^(%d+)-(%d+)-(%d+)$")
    if not year then
        return nil
    end
    return time({ year = tonumber(year), month = tonumber(month), day = tonumber(day), hour = 12 })
end

local function averageSpeed(entry)
    local seconds = tonumber(entry.seconds) or 0
    if seconds <= 0 then
        return nil
    end
    return (tonumber(entry.distanceM) or 0) / seconds * 3.6
end

-- "Name-Realm" -> "Name": the column is narrow, the tooltip has it all.
local function shortName(character)
    if type(character) ~= "string" or character == "" then
        return ""
    end
    return string.match(character, "^([^%-]+)") or character
end

----------------------------------------------------------------------------
-- Summary cards
----------------------------------------------------------------------------

local function createCard(parent, title)
    local card = Widgets.Panel(parent, nil, 0.55)
    card:SetSize(CARD_WIDTH, CARD_HEIGHT)

    local caption = Widgets.Text(card, "GameFontNormal", title, "LEFT")
    caption:SetPoint("TOPLEFT", card, "TOPLEFT", 10, -9)
    card.Sessions = Widgets.Text(card, "GameFontDisableSmall", "", "RIGHT")
    card.Sessions:SetPoint("TOPRIGHT", card, "TOPRIGHT", -10, -10)

    card.Steps = Widgets.Text(card, "GameFontHighlightLarge", "0", "LEFT")
    card.Steps:SetPoint("TOPLEFT", card, "TOPLEFT", 10, -27)
    local stepsLabel = Widgets.Text(card, "GameFontNormalSmall", "steps", "LEFT")
    stepsLabel:SetPoint("BOTTOMLEFT", card.Steps, "BOTTOMRIGHT", 4, 2)
    stepsLabel:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    card.Distance = Widgets.Text(card, "GameFontHighlightSmall", "", "LEFT")
    card.Distance:SetPoint("TOPLEFT", card, "TOPLEFT", 10, -50)
    card.Kcal = Widgets.Text(card, "GameFontHighlightSmall", "", "RIGHT")
    card.Kcal:SetPoint("TOPRIGHT", card, "TOPRIGHT", -10, -50)
    card.Time = Widgets.Text(card, "GameFontHighlightSmall", "", "LEFT")
    card.Time:SetPoint("TOPLEFT", card, "TOPLEFT", 10, -65)
    return card
end

local function fillCard(card, bucket)
    bucket = bucket or {}
    card.Steps:SetText(Walka:FormatNumber(bucket.steps))
    card.Distance:SetText(Walka:FormatDistance(bucket.distanceM))
    card.Kcal:SetText(Walka:FormatNumber(bucket.kcal) .. " kcal")
    card.Time:SetText(Walka:FormatDuration(bucket.seconds) .. " active")
    card.Sessions:SetText(plural(bucket.sessions or 0, "session", "sessions"))
end

----------------------------------------------------------------------------
-- Seven-day chart
----------------------------------------------------------------------------

local function slotTooltipTitle(self)
    return self.walkaTitle
end

local function slotTooltipBody(self)
    return self.walkaBody
end

local function createChart(parent)
    local chart = CreateFrame("Frame", nil, parent)
    chart:SetSize(INNER, CHART_HEIGHT)

    local baseline = Widgets.Fill(chart, "BORDER", 1, 1, 1, 0.15)
    baseline:SetHeight(1)
    baseline:SetPoint("BOTTOMLEFT", chart, "BOTTOMLEFT", 0, CHART_LABEL_HEIGHT - 1)
    baseline:SetPoint("BOTTOMRIGHT", chart, "BOTTOMRIGHT", 0, CHART_LABEL_HEIGHT - 1)

    -- The daily step goal, drawn across the bars.
    chart.Goal = Widgets.Fill(chart, "OVERLAY", COLOR.gold[1], COLOR.gold[2], COLOR.gold[3], 0.35)
    chart.Goal:SetHeight(1)
    chart.GoalLabel = Widgets.Text(chart, "GameFontDisableSmall", "goal", "RIGHT")
    chart.GoalLabel:SetPoint("BOTTOMRIGHT", chart.Goal, "TOPRIGHT", 0, 1)

    local slotWidth = (INNER - CHART_GUTTER) / CHART_DAYS
    for index = 1, CHART_DAYS do
        local slot = CreateFrame("Frame", nil, chart)
        slot:SetSize(slotWidth, CHART_HEIGHT)
        slot:SetPoint("BOTTOMLEFT", chart, "BOTTOMLEFT", (index - 1) * slotWidth, 0)
        slot:EnableMouse(true)

        slot.Bar = Widgets.Fill(slot, "ARTWORK", COLOR.accent[1], COLOR.accent[2], COLOR.accent[3], 0.6)
        slot.Bar:SetSize(CHART_BAR_WIDTH, 1)
        slot.Bar:SetPoint("BOTTOM", slot, "BOTTOM", 0, CHART_LABEL_HEIGHT)
        slot.Value = Widgets.Text(slot, "GameFontHighlightSmall", "", "CENTER")
        slot.Value:SetPoint("BOTTOM", slot.Bar, "TOP", 0, 2)
        slot.Label = Widgets.Text(slot, "GameFontNormalSmall", "", "CENTER")
        slot.Label:SetPoint("BOTTOM", slot, "BOTTOM", 0, 0)

        Widgets.Tooltip(slot, slotTooltipTitle, slotTooltipBody, "ANCHOR_TOP")
        slots[index] = slot
    end
    return chart
end

local function fillChart(totals)
    local days = totals.days or {}
    local goal = math.max(0, tonumber(Walka:Get("dailyStepGoal")) or 0)
    local todayKey = ns.SessionLog.dayKey(ns.Game.Now())

    local scaleMax = goal
    for index = 1, #days do
        scaleMax = math.max(scaleMax, tonumber(days[index].steps) or 0)
    end

    for index = 1, CHART_DAYS do
        local slot, day = slots[index], days[index]
        if not day then
            slot:Hide()
        else
            slot:Show()
            local steps = tonumber(day.steps) or 0
            local isToday = day.day == todayKey
            local epoch = dayEpoch(day.day)

            if steps > 0 and scaleMax > 0 then
                slot.Bar:SetHeight(math.max(2, CHART_BAR_MAX * steps / scaleMax))
                local color = (goal > 0 and steps >= goal) and COLOR.green or COLOR.accent
                slot.Bar:SetColorTexture(color[1], color[2], color[3], isToday and 0.95 or 0.6)
                slot.Bar:Show()
                slot.Value:SetText(Walka:FormatNumber(steps))
            else
                -- Keep a (hidden) sliver so the value label has an anchor.
                slot.Bar:SetHeight(1)
                slot.Bar:Hide()
                slot.Value:SetText("")
            end

            slot.Label:SetText(isToday and "Today" or (epoch and date("%a", epoch) or ""))
            local labelColor = isToday and COLOR.white or COLOR.grey
            slot.Label:SetTextColor(labelColor[1], labelColor[2], labelColor[3])

            slot.walkaTitle = epoch and date("%A, %b %d", epoch) or day.day
            if steps > 0 then
                slot.walkaBody = {
                    Walka:FormatNumber(steps) .. " steps",
                    Walka:FormatDistance(day.distanceM),
                    Walka:FormatNumber(day.kcal) .. " kcal",
                    Walka:FormatDuration(day.seconds) .. " active",
                }
                if goal > 0 then
                    local percent = math.floor(steps / goal * 100 + 0.5)
                    table.insert(slot.walkaBody, string.format("%d%% of the daily goal", percent))
                end
            else
                slot.walkaBody = "No walk recorded."
            end
        end
    end

    local chart = parts.chart
    if goal > 0 and scaleMax > 0 then
        local y = CHART_LABEL_HEIGHT + CHART_BAR_MAX * goal / scaleMax
        chart.Goal:ClearAllPoints()
        chart.Goal:SetPoint("BOTTOMLEFT", chart, "BOTTOMLEFT", 0, y)
        chart.Goal:SetPoint("BOTTOMRIGHT", chart, "BOTTOMRIGHT", 0, y)
        chart.Goal:Show()
        chart.GoalLabel:Show()
    else
        chart.Goal:Hide()
        chart.GoalLabel:Hide()
    end
end

----------------------------------------------------------------------------
-- Session list
----------------------------------------------------------------------------

local function rowTooltipTitle(self)
    local entry = self.walkaEntry
    if not entry then
        return nil
    end
    local title = date("%a %b %d, %H:%M", entry.start)
    local finish = tonumber(entry["end"]) or 0
    if finish > entry.start then
        title = title .. " - " .. date("%H:%M", finish)
    end
    return title
end

local function rowTooltipBody(self)
    local entry = self.walkaEntry
    if not entry then
        return nil
    end
    local lines = {}
    if entry.measured then
        lines[#lines + 1] = Widgets.Colorize("Measured by the treadmill (from the companion app).", COLOR.green)
    elseif entry.unconfirmed then
        lines[#lines + 1] = Widgets.Colorize(
            "Not counted. The companion app has synced since this session and has no walk on the "
                .. "treadmill for it: it was not running, the pad was off, or the walk was deleted there.",
            COLOR.grey
        )
    else
        lines[#lines + 1] = Widgets.Colorize(
            "Estimated by the addon. The treadmill's measured numbers replace it once the "
                .. "companion app has synced and you /reload.",
            COLOR.grey
        )
    end
    if (tonumber(entry.maxKmh) or 0) > 0 then
        lines[#lines + 1] = "Top speed: " .. Walka:FormatSpeed(entry.maxKmh)
    end
    if tonumber(entry.avgHr) then
        lines[#lines + 1] = string.format("Average heart rate: %d bpm", entry.avgHr)
    end
    if tonumber(entry.maxHr) then
        lines[#lines + 1] = string.format("Peak heart rate: %d bpm", entry.maxHr)
    end
    if type(entry.stepsSource) == "string" and entry.stepsSource ~= "" then
        lines[#lines + 1] = "Steps counted by: " .. entry.stepsSource
    end
    if type(entry.character) == "string" and entry.character ~= "" then
        lines[#lines + 1] = "Character: " .. entry.character
    end
    if (tonumber(entry.yards) or 0) > 0 then
        lines[#lines + 1] = "Your character travelled " .. Walka:FormatDistance(entry.yards * METERS_PER_YARD) .. "."
    end
    return lines
end

local function createRow(parent, index)
    local row = CreateFrame("Button", nil, parent)
    row:SetSize(LIST_WIDTH, ROW_HEIGHT)
    row:SetPoint("TOPLEFT", parent, "TOPLEFT", 0, -(index - 1) * ROW_HEIGHT)

    -- Faint stripes make the columns easier to follow.
    if index % 2 == 0 then
        local stripe = Widgets.Fill(row, "BACKGROUND", 1, 1, 1, 0.035)
        stripe:SetAllPoints(row)
    end
    row.Hover = Widgets.Fill(row, "BORDER", 1, 1, 1, 0.10)
    row.Hover:SetAllPoints(row)
    row.Hover:Hide()

    -- First column: a green dot for measured sessions, "~" for estimates.
    row.Dot = Widgets.Dot(row, 6, COLOR.green)
    row.Dot:SetPoint("CENTER", row, "LEFT", 8, 0)
    row.Tilde = Widgets.Text(row, "GameFontHighlightSmall", "~", "CENTER")
    row.Tilde:SetPoint("CENTER", row, "LEFT", 8, 0)
    row.Tilde:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    row.cells = {}
    for _, column in ipairs(COLUMNS) do
        local cell = Widgets.Text(row, "GameFontHighlightSmall", "", column.justify)
        cell:SetPoint("LEFT", row, "LEFT", column.x, 0)
        cell:SetWidth(column.width)
        cell:SetWordWrap(false)
        row.cells[column.key] = cell
    end

    row:SetScript("OnEnter", function(self)
        self.Hover:Show()
    end)
    row:SetScript("OnLeave", function(self)
        self.Hover:Hide()
    end)
    Widgets.Tooltip(row, rowTooltipTitle, rowTooltipBody, "ANCHOR_RIGHT")
    return row
end

local function fillRow(row, entry)
    row.walkaEntry = entry
    if not entry then
        row:Hide()
        return
    end
    row:Show()
    row.Dot:SetShown(entry.measured and true or false)
    row.Tilde:SetShown(not entry.measured)

    local cells = row.cells
    local speed = averageSpeed(entry)
    cells.when:SetText(date("%b %d %H:%M", entry.start))
    cells.time:SetText(Walka:FormatDuration(entry.seconds))
    cells.distance:SetText(Walka:FormatDistance(entry.distanceM))
    cells.steps:SetText(Walka:FormatNumber(entry.steps))
    cells.kcal:SetText(Walka:FormatNumber(entry.kcal))
    cells.speed:SetText(speed and Walka:FormatSpeed(speed) or "-")
    cells.who:SetText(shortName(entry.character))

    -- Estimates are shown a little dimmer than measurements, and sessions
    -- that count for nothing dimmer still.
    local shade = entry.measured and 1 or (entry.unconfirmed and 0.5 or 0.8)
    for _, cell in pairs(cells) do
        cell:SetTextColor(shade, shade, shade)
    end
end

local function maxOffset()
    return math.max(0, #sessions - ROWS)
end

local function showRows()
    offset = Widgets.Clamp(offset, 0, maxOffset())
    for index = 1, ROWS do
        fillRow(rows[index], sessions[offset + index])
    end
    parts.scrollBar:SetValue(offset)

    local count = #sessions
    if count == 0 then
        parts.count:SetText("")
    elseif count <= ROWS then
        parts.count:SetText(plural(count, "session", "sessions"))
    else
        local last = math.min(count, offset + ROWS)
        parts.count:SetText(string.format("%d-%d of %s", offset + 1, last, Walka:FormatNumber(count)))
    end
end

local function scrollTo(newOffset)
    newOffset = Widgets.Clamp(math.floor((newOffset or 0) + 0.5), 0, maxOffset())
    if newOffset ~= offset then
        offset = newOffset
        showRows()
    else
        -- Keep the thumb on a whole row while it is being dragged.
        parts.scrollBar:SetValue(offset)
    end
end

----------------------------------------------------------------------------
-- Refresh
----------------------------------------------------------------------------

-- Totals over `sessions`, the list merged at the last refresh, plus the
-- session in progress. The list only changes when "HISTORY" fires (which
-- marks the window stale), so nothing is merged again here.
local function showTotals()
    local totals = Walka:GetTotals(sessions)
    lastTotalsAt = GetTime()
    fillCard(cards.today, totals.today)
    fillCard(cards.week, totals.week)
    fillCard(cards.all, totals.all)

    local streak = tonumber(totals.streak) or 0
    local streakText = streak > 0 and (plural(streak, "day", "days") .. " in a row") or "not yet - walk today to start one"
    parts.streak:SetText(Widgets.Colorize("Streak  ", COLOR.gold) .. streakText)

    local best = totals.best or {}
    if (tonumber(best.steps) or 0) > 0 then
        parts.best:SetText(
            Widgets.Colorize("Session bests  ", COLOR.gold)
                .. Walka:FormatNumber(best.steps) .. " steps, "
                .. Walka:FormatDistance(best.distanceM) .. ", "
                .. Walka:FormatDuration(best.seconds)
        )
    else
        parts.best:SetText("")
    end

    fillChart(totals)
end

-- "Your characters ran 12.40 mi while you walked 1.30 mi."
local function funLine()
    local yards, walkedM = 0, 0
    for index = 1, #sessions do
        local entry = sessions[index]
        local entryYards = tonumber(entry.yards) or 0
        if entryYards > 0 then
            yards = yards + entryYards
            walkedM = walkedM + (tonumber(entry.distanceM) or 0)
        end
    end
    if yards <= 0 or walkedM <= 0 then
        return ""
    end
    local ranM = yards * METERS_PER_YARD
    local text = string.format(
        "Your characters ran %s while you walked %s.",
        Walka:FormatDistance(ranM),
        Walka:FormatDistance(walkedM)
    )
    local ratio = ranM / walkedM
    if ratio >= 2 then
        text = text .. string.format(" That is %.0f times as far.", ratio)
    end
    return text
end

local EMPTY_TEXT = "No walks yet.\n\nStart one with /walka start or the play button on the HUD.\n"
    .. "Walka then counts your walk, and the session shows up here when you stop."
local EMPTY_TEXT_WALKING = "Your first session is under way.\n\nIt shows up here when you stop it."

local function showSessions()
    local count = #sessions
    parts.empty:SetShown(count == 0)
    if count == 0 then
        parts.empty:SetText(Walka:GetSession() and EMPTY_TEXT_WALKING or EMPTY_TEXT)
    end
    parts.legend:SetShown(count > 0)
    parts.scrollBar:SetRange(maxOffset(), count > 0 and ROWS / count or 1, ROWS)
    showRows()
    parts.fun:SetText(funLine())
end

local function refresh()
    if not frame or not frame:IsShown() then
        return
    end
    stale = false
    -- The one merge of a refresh; the totals and the list both use it.
    sessions = Walka:GetHistory() or {}
    showTotals()
    showSessions()
end

----------------------------------------------------------------------------
-- Construction
----------------------------------------------------------------------------

local function create()
    if frame then
        return
    end
    frame = Widgets.Panel(UIParent, "WalkaHistoryFrame", 0.94)
    frame:SetSize(WIDTH, HEIGHT)
    frame:SetPoint("CENTER", UIParent, "CENTER", 0, 30)
    frame:SetFrameStrata("HIGH")
    frame:SetToplevel(true)
    frame:Hide()
    Widgets.MakeMovable(frame)

    -- Escape closes it, like the game's own windows.
    if type(UISpecialFrames) == "table" then
        table.insert(UISpecialFrames, "WalkaHistoryFrame")
    end

    local title = Widgets.Text(frame, "GameFontNormalLarge", "Walka", "LEFT")
    title:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN + 2, -11)
    local subtitle = Widgets.Text(frame, "GameFontHighlight", "walking history", "LEFT")
    subtitle:SetPoint("BOTTOMLEFT", title, "BOTTOMRIGHT", 6, 1)
    subtitle:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    local close = Widgets.CloseButton(frame, function()
        frame:Hide()
    end)
    close:ClearAllPoints()
    close:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -3, -3)

    -- An addon cannot receive anything while the game runs: the walks the
    -- companion app has measured only arrive when the interface loads. This
    -- button reloads it (a click counts as the key press the game requires).
    -- A session in progress survives the reload.
    local sync = Widgets.Button(frame, "Sync", 58, function()
        if ReloadUI then
            ReloadUI()
        end
    end)
    sync:SetPoint("RIGHT", close, "LEFT", -6, 0)
    Widgets.Tooltip(sync, "Load measured walks", function()
        local lines = {
            "Reloads the interface (a few seconds) to load the walks the companion app "
                .. "has measured since you logged in. They replace the ~ estimates.",
            "A session in progress carries on.",
        }
        local synced = Walka:GetSyncTime()
        if synced then
            lines[#lines + 1] = Widgets.Colorize("Companion data from " .. date("%b %d %H:%M", synced) .. ".", COLOR.grey)
        else
            lines[#lines + 1] = Widgets.Colorize("No companion data loaded yet.", COLOR.grey)
        end
        return lines
    end, "ANCHOR_BOTTOMRIGHT")
    parts.sync = sync

    -- The character's journey has its own window (UI/Journey.lua).
    local journey = Widgets.Button(frame, "Journey", 68, function()
        if ns.JourneyWindow then
            ns.JourneyWindow.Show()
        end
    end)
    journey:SetPoint("RIGHT", sync, "LEFT", -6, 0)
    Widgets.Tooltip(
        journey,
        "Your character's journey",
        "Level, time played, and how far you and your character have walked - with a line on how to share it.",
        "ANCHOR_BOTTOMRIGHT"
    )

    -- Summary cards.
    local cardTitles = { { "today", "Today" }, { "week", "Last 7 days" }, { "all", "All time" } }
    for index, info in ipairs(cardTitles) do
        local card = createCard(frame, info[2])
        card:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN + (index - 1) * (CARD_WIDTH + CARD_GAP), CARDS_TOP)
        cards[info[1]] = card
    end

    parts.streak = Widgets.Text(frame, "GameFontHighlightSmall", "", "LEFT")
    parts.streak:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN + 2, CARDS_TOP - CARD_HEIGHT - 8)
    parts.best = Widgets.Text(frame, "GameFontHighlightSmall", "", "RIGHT")
    parts.best:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -MARGIN - 2, CARDS_TOP - CARD_HEIGHT - 8)

    -- Chart.
    parts.chart = createChart(frame)
    parts.chart:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, CHART_TOP)

    -- List header.
    local header = CreateFrame("Frame", nil, frame)
    header:SetSize(LIST_WIDTH, 14)
    header:SetPoint("TOPLEFT", frame, "TOPLEFT", MARGIN, LIST_TOP)
    for _, column in ipairs(COLUMNS) do
        local label = Widgets.Text(header, "GameFontNormalSmall", column.title, column.justify)
        label:SetPoint("LEFT", header, "LEFT", column.x, 0)
        label:SetWidth(column.width)
    end
    local headerRule = Widgets.Fill(header, "ARTWORK", COLOR.gold[1], COLOR.gold[2], COLOR.gold[3], 0.25)
    headerRule:SetHeight(1)
    headerRule:SetPoint("BOTTOMLEFT", header, "BOTTOMLEFT", 0, -1)
    headerRule:SetPoint("BOTTOMRIGHT", header, "BOTTOMRIGHT", 0, -1)

    -- List body: a fixed pool of rows.
    local list = CreateFrame("Frame", nil, frame)
    list:SetSize(LIST_WIDTH, ROWS * ROW_HEIGHT)
    list:SetPoint("TOPLEFT", header, "BOTTOMLEFT", 0, -2)
    list:EnableMouseWheel(true)
    list:SetScript("OnMouseWheel", function(_, delta)
        scrollTo(offset - (delta or 0) * 2)
    end)
    for index = 1, ROWS do
        rows[index] = createRow(list, index)
    end

    parts.scrollBar = Widgets.ScrollBar(frame, scrollTo)
    parts.scrollBar:SetPoint("TOPLEFT", list, "TOPRIGHT", 4, 0)
    parts.scrollBar:SetPoint("BOTTOMLEFT", list, "BOTTOMRIGHT", 4, 0)

    parts.empty = Widgets.Text(frame, "GameFontHighlight", "", "CENTER")
    parts.empty:SetPoint("CENTER", list, "CENTER", 0, 0)
    parts.empty:SetWidth(LIST_WIDTH - 60)
    parts.empty:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])
    parts.empty:Hide()

    -- Footer: the fun line, the legend and the position in the list.
    parts.fun = Widgets.Text(frame, "GameFontNormal", "", "LEFT")
    parts.fun:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", MARGIN + 2, 30)
    parts.fun:SetWidth(INNER - 4)
    parts.fun:SetWordWrap(false)

    parts.legend = CreateFrame("Frame", nil, frame)
    parts.legend:SetSize(340, 12)
    parts.legend:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", MARGIN + 2, 11)
    local legendDot = Widgets.Dot(parts.legend, 6, COLOR.green)
    legendDot:SetPoint("LEFT", parts.legend, "LEFT", 1, 0)
    local legendText = Widgets.Text(
        parts.legend,
        "GameFontDisableSmall",
        "measured by the treadmill      ~  estimated by the addon",
        "LEFT"
    )
    legendText:SetPoint("LEFT", legendDot, "RIGHT", 5, 0)

    parts.count = Widgets.Text(frame, "GameFontDisableSmall", "", "RIGHT")
    parts.count:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -MARGIN - 2, 11)

    Widgets.Hook(frame, "OnShow", function(self)
        self:Raise()
        offset = 0
        refresh()
    end)
end

----------------------------------------------------------------------------
-- Public API
----------------------------------------------------------------------------

function History.Show()
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

function History.Hide()
    if frame and frame:IsShown() then
        if PlaySound and SOUNDKIT and SOUNDKIT.IG_CHARACTER_INFO_CLOSE then
            PlaySound(SOUNDKIT.IG_CHARACTER_INFO_CLOSE)
        end
        frame:Hide()
    end
end

function History.Toggle()
    if frame and frame:IsShown() then
        History.Hide()
    else
        History.Show()
    end
end

----------------------------------------------------------------------------
-- Wiring
----------------------------------------------------------------------------
-- A refresh merges the whole history, and events come in bursts (stopping a
-- session fires "HISTORY" and "SESSION"; dragging a slider in the options
-- fires "SETTINGS" many times a second). So events only mark the window as
-- stale, and the next UI tick - at most a quarter of a second later - redraws
-- it once, with one merge.

local shownUnits, shownGoal

local function markStale()
    stale = true
end

Walka:On("HISTORY", markStale)
Walka:On("SESSION", markStale)

-- Only the display unit and the step goal matter to this window.
Walka:On("SETTINGS", function()
    local units, goal = Walka:Get("units"), Walka:Get("dailyStepGoal")
    if units ~= shownUnits or goal ~= shownGoal then
        shownUnits, shownGoal = units, goal
        stale = true
    end
end)

Walka:On("TICK", function()
    if not frame or not frame:IsShown() then
        return
    end
    if stale then
        refresh()
    elseif GetTime() - lastTotalsAt >= (Walka:GetSession() and LIVE_REFRESH_SECONDS or IDLE_REFRESH_SECONDS) then
        -- The session in progress is part of the totals: keep them moving.
        showTotals()
    end
end)

return History
