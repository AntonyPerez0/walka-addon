-- Walka HUD: the small window you glance at while walking and playing.
--
--   Walka  * Walking            [>] [#]  [h] [o]
--   ~2.0 mph  > 2.5                 This session
--   ~1,234 steps                         ~56 kcal
--   ~0.52 mi                                23:45
--   [======= today's steps / daily goal ========]
--
-- With no session running it shows today's totals instead, so it is still
-- worth keeping on screen. The frame is an ordinary (unprotected) frame: it
-- can be shown, hidden and moved in combat.

local ADDON_NAME, ns = ...

local Walka = ns.Walka
local Widgets = ns.Widgets
local COLOR = Widgets.COLOR

local HUD = {}
ns.HUD = HUD

local WIDTH = 224
local HEIGHT = 116          -- with the daily-goal bar
local HEIGHT_NO_BAR = 104   -- without it (goal switched off)
local BAR_WIDTH = WIDTH - 18

local MIN_SCALE, MAX_SCALE = 0.6, 1.6
local MIN_ALPHA, MAX_ALPHA = 0.3, 1.0

-- Where the HUD sits until the user drags it: left edge, a little above the
-- middle - clear of the unit frames, the chat and the action bars.
local DEFAULT_POINT = { "LEFT", "LEFT", 40, 80 }

local VALID_POINT = {
    TOPLEFT = true, TOP = true, TOPRIGHT = true,
    LEFT = true, CENTER = true, RIGHT = true,
    BOTTOMLEFT = true, BOTTOM = true, BOTTOMRIGHT = true,
}

local frame          -- the HUD; nil until the core has loaded
local parts = {}     -- its font strings, textures and buttons

----------------------------------------------------------------------------
-- Session state
----------------------------------------------------------------------------

local STATES = {
    off = { text = "Off", color = COLOR.grey },
    walking = { text = "Walking", color = COLOR.green },
    -- the treadmill counts down before the belt moves
    starting = { text = "Starting", color = COLOR.accent },
    -- session running, but the belt is stopped because the character stands
    -- still and "stop the belt when standing" is on
    waiting = { text = "Waiting", color = COLOR.accent },
    paused = { text = "Paused", color = COLOR.amber },
    afk = { text = "Paused (AFK)", color = COLOR.amber },
}

local function currentState(session, beltKmh, targetKmh)
    if not session then
        return "off"
    end
    if session.paused then
        return session.pauseReason == "afk" and "afk" or "paused"
    end
    if beltKmh > 0 then
        return "walking"
    end
    if targetKmh > 0 then
        return "starting"
    end
    return "waiting"
end

----------------------------------------------------------------------------
-- Totals cache
----------------------------------------------------------------------------
-- Merging the session history (Walka:GetHistory) is far too much work for
-- four redraws a second. So the totals are kept and recomputed only when the
-- history or the session changes (and once a minute, to notice midnight),
-- from one merge each time. GetTotals already counts the session in
-- progress, so between refreshes only what the session has gained *since
-- the snapshot* is added.

local TOTALS_MAX_AGE = 60

local cache = {
    totals = nil,       -- last Walka:GetTotals()
    at = 0,             -- GetTime() of that call
    dirty = true,
    session = nil,      -- the live session at that moment (table identity)
    steps = 0,          -- ... and its numbers then
    kcal = 0,
    distanceM = 0,
    seconds = 0,
    estimated = false,  -- do today's totals include addon estimates?
}

-- True if any of today's finished sessions is an addon estimate rather than
-- a treadmill measurement. `history` is the merged list, newest first.
local function todayHasEstimates(history)
    local now = ns.Game.Now()
    local dayKey = ns.SessionLog.dayKey
    local today = dayKey(now)
    for index = 1, #history do
        local entry = history[index]
        local start = tonumber(entry.start) or 0
        -- Newest first: once we are more than a day back, nothing is today's.
        if start < now - 86400 then
            break
        end
        if not entry.measured and dayKey(start) == today then
            return true
        end
    end
    return false
end

local function refreshTotals()
    local now = GetTime()
    if not cache.dirty and now - cache.at < TOTALS_MAX_AGE then
        return
    end
    -- Marked clean first: should the history ever fail to add up, one error
    -- report a minute is enough - not one on every redraw.
    cache.at = now
    cache.dirty = false
    local history = Walka:GetHistory()
    cache.totals = Walka:GetTotals(history)
    local session = Walka:GetSession()
    cache.session = session
    cache.steps = session and session.steps or 0
    cache.kcal = session and session.kcal or 0
    cache.distanceM = session and session.distanceM or 0
    cache.seconds = session and session.seconds or 0
    cache.estimated = session ~= nil or todayHasEstimates(history)
end

-- Today's steps, kcal, metres and seconds, including the session's progress
-- since the cache was filled.
local function todayNumbers()
    local today = cache.totals and cache.totals.today
    if not today then
        return 0, 0, 0, 0
    end
    local steps, kcal, distanceM, seconds = today.steps or 0, today.kcal or 0, today.distanceM or 0, today.seconds or 0
    local session = Walka:GetSession()
    if session and session == cache.session then
        steps = steps + (session.steps - cache.steps)
        kcal = kcal + (session.kcal - cache.kcal)
        distanceM = distanceM + (session.distanceM - cache.distanceM)
        seconds = seconds + (session.seconds - cache.seconds)
    end
    return steps, kcal, distanceM, seconds
end

----------------------------------------------------------------------------
-- Settings-derived values (refreshed on "SETTINGS", read on every tick)
----------------------------------------------------------------------------

local stepKmh = 0.1     -- the treadmill's speed increment
local stepGoal = 0      -- daily step goal, 0 = off

----------------------------------------------------------------------------
-- Redraw
----------------------------------------------------------------------------

local function grey(text)
    return Widgets.Colorize(text, COLOR.grey)
end

local STEPS_LABEL, KCAL_LABEL = grey(" steps"), grey(" kcal")

-- " mi" / " km" in grey, made once per unit rather than on every redraw.
local distanceLabels = {}

local function distanceLabel(unit)
    local label = distanceLabels[unit]
    if not label then
        label = grey(" " .. unit)
        distanceLabels[unit] = label
    end
    return label
end

local function update()
    if not frame or not frame:IsShown() then
        return
    end
    refreshTotals()

    local session = Walka:GetSession()
    local beltKmh, targetKmh = Walka:GetLiveSpeed()
    beltKmh, targetKmh = beltKmh or 0, targetKmh or 0
    local state = currentState(session, beltKmh, targetKmh)
    local info = STATES[state]

    -- Header.
    parts.dot:SetColor(info.color[1], info.color[2], info.color[3])
    parts.state:SetText(info.text)
    parts.toggle:SetGlyph((session and not session.paused) and "pause" or "play")
    parts.stop:SetActive(session ~= nil)

    -- Belt speed. With a session running it is the addon's estimate of what
    -- the belt is doing, hence the "~".
    parts.speed:SetText(Walka:FormatSpeed(beltKmh, true))
    parts.unit:SetText(Walka:SpeedUnit())
    parts.tilde:SetText(session and "~" or "")
    local speedColor = COLOR.white
    if state == "off" then
        speedColor = COLOR.grey
    elseif state == "paused" or state == "afk" then
        speedColor = COLOR.amber
    end
    parts.speed:SetTextColor(speedColor[1], speedColor[2], speedColor[3])

    -- Where the belt is heading, when that is not where it is.
    local heading = session and not session.paused and math.abs(targetKmh - beltKmh) > stepKmh
    parts.hint:SetShown(heading and true or false)
    if heading then
        parts.hintText:SetText(Walka:FormatSpeed(targetKmh, true))
    end

    -- Numbers: the session's while one is running, otherwise today's.
    local steps, kcal, distanceM, seconds, estimated
    local todaySteps = todayNumbers()
    if session then
        steps, kcal, distanceM, seconds = session.steps, session.kcal, session.distanceM, session.seconds
        estimated = true
        parts.caption:SetText("This session")
    else
        steps, kcal, distanceM, seconds = todayNumbers()
        estimated = cache.estimated
        parts.caption:SetText("Today")
    end
    local mark = estimated and "~" or ""
    local distance, distanceUnit = string.match(Walka:FormatDistance(distanceM), "^(%S+)%s*(.*)$")
    parts.steps:SetText(mark .. Walka:FormatNumber(steps) .. STEPS_LABEL)
    parts.kcal:SetText(mark .. Walka:FormatNumber(kcal) .. KCAL_LABEL)
    parts.distance:SetText(mark .. (distance or "0") .. distanceLabel(distanceUnit or ""))
    parts.time:SetText(Walka:FormatDuration(seconds))

    -- Daily goal.
    if stepGoal > 0 then
        local fraction = Widgets.Clamp(todaySteps / stepGoal, 0, 1)
        local color = fraction >= 1 and COLOR.green or COLOR.accent
        parts.barFill:SetColorTexture(color[1], color[2], color[3], 0.9)
        parts.barFill:SetWidth(math.max(1, BAR_WIDTH * fraction))
        parts.barFill:SetShown(fraction > 0)
    end
end

----------------------------------------------------------------------------
-- Tooltip
----------------------------------------------------------------------------

local STATE_HELP = {
    off = "No session is running. The numbers are today's totals.",
    walking = "A session is running: the belt follows your character.",
    starting = "The treadmill is counting down; the belt starts at its slowest speed.",
    waiting = "Your character is standing still, so the belt is stopped. It restarts when you move.",
    paused = "The session is paused and the belt is stopped.",
    afk = "You are AFK, so the session is paused. It resumes when you come back.",
}

local function tooltipBody()
    local lines = {}
    local session = Walka:GetSession()
    local beltKmh, targetKmh = Walka:GetLiveSpeed()
    local state = currentState(session, beltKmh or 0, targetKmh or 0)
    lines[#lines + 1] = Widgets.Colorize(STATE_HELP[state], COLOR.white)

    if session and not session.paused then
        lines[#lines + 1] = "Target speed: " .. Walka:FormatSpeed(targetKmh or 0) .. "."
    end
    if stepGoal > 0 then
        refreshTotals()
        local todaySteps = todayNumbers()
        lines[#lines + 1] = string.format(
            "Daily goal: %s of %s steps (%d%%).",
            Walka:FormatNumber(todaySteps),
            Walka:FormatNumber(stepGoal),
            math.floor(todaySteps / stepGoal * 100 + 0.5)
        )
    end

    lines[#lines + 1] = ""
    lines[#lines + 1] = Widgets.Colorize(
        "Numbers marked ~ are the addon's live estimates. The treadmill's measured numbers "
            .. "arrive from the companion app after /reload.",
        COLOR.grey
    )
    local synced = Walka:GetSyncTime()
    if synced then
        lines[#lines + 1] = Widgets.Colorize("Companion data from " .. date("%b %d %H:%M", synced) .. ".", COLOR.grey)
    else
        lines[#lines + 1] = Widgets.Colorize(
            "No companion data yet - run the companion app, then /reload.",
            COLOR.grey
        )
    end

    local gameState = Walka:GetGameState()
    if gameState and gameState.estimated then
        lines[#lines + 1] = Widgets.Colorize(
            "In combat the game hides your character's real speed from addons, so Walka is estimating it.",
            COLOR.amber
        )
    end

    lines[#lines + 1] = ""
    if Walka:Get("hud.locked") then
        lines[#lines + 1] = Widgets.Colorize("Locked. Right-click for the menu.", COLOR.green)
    else
        lines[#lines + 1] = Widgets.Colorize("Drag to move. Right-click for the menu.", COLOR.green)
    end
    return lines
end

-- Open the tooltip towards the middle of the screen.
local function tooltipAnchor(self)
    local centre = self:GetCenter()
    local screen = UIParent:GetWidth()
    if centre and screen and centre * self:GetScale() > screen / 2 then
        return "ANCHOR_LEFT"
    end
    return "ANCHOR_RIGHT"
end

----------------------------------------------------------------------------
-- Position
----------------------------------------------------------------------------
-- Saved as { point, relativePoint, x, y } relative to UIParent, with the
-- offsets in UIParent's units (i.e. multiplied by the HUD's scale), so that
-- changing the HUD's scale later does not move its anchor.

-- A saved offset as a number SetPoint can take, or nil. The saved variables
-- are a file on disk: whatever is in it, an anchor must not come out as
-- not-a-number, infinite, or light-years off the screen. (The frame is
-- clamped to the screen, so anything in this range ends up visible.)
local MAX_OFFSET = 20000

local function savedOffset(value)
    value = tonumber(value)
    if value and value >= -MAX_OFFSET and value <= MAX_OFFSET then
        return value
    end
    return nil
end

local function applyPosition()
    if frame.walkaMoving then
        return
    end
    local saved = Walka:Get("hud.point")
    local point, x, y = DEFAULT_POINT, DEFAULT_POINT[3], DEFAULT_POINT[4]
    if type(saved) == "table" and VALID_POINT[saved[1]] and VALID_POINT[saved[2]] then
        local savedX, savedY = savedOffset(saved[3]), savedOffset(saved[4])
        if savedX and savedY then
            point, x, y = saved, savedX, savedY
        end
    end
    local scale = frame:GetScale()
    if not scale or scale <= 0 then
        scale = 1
    end
    frame:ClearAllPoints()
    frame:SetPoint(point[1], UIParent, point[2], x / scale, y / scale)
end

-- Describe where the HUD is now, anchored to the nearest edge or corner of
-- the screen so that it stays there if the resolution changes.
local function currentPoint()
    local scale = frame:GetScale() or 1
    local left, right, top, bottom = frame:GetLeft(), frame:GetRight(), frame:GetTop(), frame:GetBottom()
    local width, height = UIParent:GetWidth(), UIParent:GetHeight()
    if not (left and right and top and bottom and width and height) or width <= 0 or height <= 0 then
        return nil
    end
    -- Frame edges in UIParent's units, relative to UIParent's bottom-left.
    local originX, originY = UIParent:GetLeft() or 0, UIParent:GetBottom() or 0
    left, right = left * scale - originX, right * scale - originX
    top, bottom = top * scale - originY, bottom * scale - originY
    local centreX, centreY = (left + right) / 2, (top + bottom) / 2

    local horizontal = centreX < width / 3 and "LEFT" or centreX > width * 2 / 3 and "RIGHT" or ""
    local vertical = centreY > height * 2 / 3 and "TOP" or centreY < height / 3 and "BOTTOM" or ""
    local point = vertical .. horizontal
    if point == "" then
        point = "CENTER"
    end
    local x = horizontal == "LEFT" and left or horizontal == "RIGHT" and right - width or centreX - width / 2
    local y = vertical == "TOP" and top - height or vertical == "BOTTOM" and bottom or centreY - height / 2
    return { point, point, math.floor(x + 0.5), math.floor(y + 0.5) }
end

local function savePosition()
    local point = currentPoint()
    if point then
        -- Fires "SETTINGS", which re-anchors the frame to the saved point.
        Walka:Set("hud.point", point)
    end
end

----------------------------------------------------------------------------
-- Settings
----------------------------------------------------------------------------

local function applySettings()
    if not frame then
        return
    end
    local scale = Widgets.Clamp(tonumber(Walka:Get("hud.scale")) or 1, MIN_SCALE, MAX_SCALE)
    local alpha = Widgets.Clamp(tonumber(Walka:Get("hud.alpha")) or 0.9, MIN_ALPHA, MAX_ALPHA)
    frame:SetScale(scale)
    frame:SetAlpha(alpha)
    applyPosition()

    stepKmh = Walka:GetBeltLimits().stepKmh or 0.1
    stepGoal = math.max(0, tonumber(Walka:Get("dailyStepGoal")) or 0)
    parts.barBack:SetShown(stepGoal > 0)
    if stepGoal <= 0 then
        parts.barFill:Hide()
    end
    frame:SetHeight(stepGoal > 0 and HEIGHT or HEIGHT_NO_BAR)

    frame:SetShown(Walka:Get("hud.shown") ~= false)
end

----------------------------------------------------------------------------
-- Context menu
----------------------------------------------------------------------------

local function openMenu(owner)
    -- The client's menu system (Blizzard_Menu).
    if not (MenuUtil and MenuUtil.CreateContextMenu) then
        return
    end
    MenuUtil.CreateContextMenu(owner, function(_, root)
        root:CreateTitle("Walka")
        root:CreateCheckbox("Lock position", function()
            return Walka:Get("hud.locked") == true
        end, function()
            Walka:Set("hud.locked", not Walka:Get("hud.locked"))
        end)
        root:CreateButton("History", function()
            if ns.History then
                ns.History.Show()
            end
        end)
        root:CreateButton("Options", function()
            if ns.Options then
                ns.Options.Open()
            end
        end)
        root:CreateDivider()
        root:CreateButton("Hide", function()
            HUD.SetShown(false)
        end)
    end)
end

----------------------------------------------------------------------------
-- Construction
----------------------------------------------------------------------------

local function create()
    if frame then
        return
    end
    frame = Widgets.Panel(UIParent, "WalkaHUD", 0.82)
    frame:SetSize(WIDTH, HEIGHT)
    frame:SetFrameStrata("MEDIUM")
    frame:Hide()

    Widgets.MakeMovable(frame, function()
        return not Walka:Get("hud.locked")
    end, savePosition)
    frame:SetScript("OnMouseUp", function(self, mouseButton)
        if mouseButton == "RightButton" then
            openMenu(self)
        end
    end)
    Widgets.Tooltip(frame, "Walka", tooltipBody, tooltipAnchor)
    -- Do not leave the tooltip behind when the HUD is hidden under the mouse.
    Widgets.Hook(frame, "OnHide", function(self)
        Widgets.HideTooltip(self)
    end)

    -- Header: title, state, buttons.
    local title = Widgets.Text(frame, "GameFontNormal", "Walka", "LEFT")
    title:SetPoint("LEFT", frame, "TOPLEFT", 9, -15)

    parts.dot = Widgets.Dot(frame, 7, COLOR.grey)
    parts.dot:SetPoint("LEFT", title, "RIGHT", 7, 0)

    parts.state = Widgets.Text(frame, "GameFontHighlightSmall", "", "LEFT")
    parts.state:SetPoint("LEFT", parts.dot, "RIGHT", 4, 0)
    parts.state:SetWidth(72)
    parts.state:SetWordWrap(false)

    parts.options = Widgets.GlyphButton(frame, 16, "sliders", function()
        if ns.Options then
            ns.Options.Open()
        end
    end)
    parts.options:SetPoint("RIGHT", frame, "TOPRIGHT", -8, -15)
    Widgets.Tooltip(parts.options, "Options", "Speed mapping, units, your profile and more.")

    parts.history = Widgets.GlyphButton(frame, 16, "chart", function()
        if ns.History then
            ns.History.Toggle()
        end
    end)
    parts.history:SetPoint("RIGHT", parts.options, "LEFT", -3, 0)
    Widgets.Tooltip(parts.history, "History", "Past sessions, totals and your week.")

    parts.stop = Widgets.GlyphButton(frame, 16, "stop", function()
        if Walka:GetSession() then
            Walka:StopSession()
        end
    end)
    parts.stop:SetPoint("RIGHT", parts.history, "LEFT", -7, 0)
    Widgets.Tooltip(parts.stop, "Stop", function()
        if Walka:GetSession() then
            return "Ends the session, stops the belt and files the session in the history."
        end
        return "No session is running."
    end)

    parts.toggle = Widgets.GlyphButton(frame, 16, "play", function()
        Walka:TogglePause()
    end)
    parts.toggle:SetPoint("RIGHT", parts.stop, "LEFT", -3, 0)
    Widgets.Tooltip(parts.toggle, function()
        local session = Walka:GetSession()
        if not session then
            return "Start walking"
        end
        return session.paused and "Resume" or "Pause"
    end, function()
        local session = Walka:GetSession()
        if not session then
            return "Starts a session: after the treadmill's countdown the belt follows your character's speed."
        elseif session.paused then
            return "The belt restarts at its slowest speed after the treadmill's countdown."
        end
        return "Stops the belt and keeps the session open."
    end)

    local rule = Widgets.Fill(frame, "ARTWORK", 1, 1, 1, 0.08)
    rule:SetHeight(1)
    rule:SetPoint("TOPLEFT", frame, "TOPLEFT", 8, -26)
    rule:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -8, -26)

    -- Belt speed. The "~" keeps its own fixed-width slot so that the number
    -- does not jump sideways when it appears.
    parts.tilde = Widgets.Text(frame, "GameFontNormal", "", "RIGHT")
    parts.tilde:SetPoint("TOPLEFT", frame, "TOPLEFT", 8, -40)
    parts.tilde:SetWidth(10)
    parts.tilde:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    parts.speed = Widgets.Text(frame, "NumberFontNormalHuge", "0.0", "LEFT")
    parts.speed:SetPoint("TOPLEFT", frame, "TOPLEFT", 19, -31)

    parts.unit = Widgets.Text(frame, "GameFontNormalSmall", "", "LEFT")
    parts.unit:SetPoint("BOTTOMLEFT", parts.speed, "BOTTOMRIGHT", 3, 5)

    -- Target hint: a small arrow and the speed the belt is heading for.
    parts.hint = CreateFrame("Frame", nil, frame)
    parts.hint:SetSize(44, 12)
    parts.hint:SetPoint("BOTTOMLEFT", parts.unit, "BOTTOMRIGHT", 7, -1)
    local arrow = Widgets.Fill(parts.hint, "ARTWORK", COLOR.accent[1], COLOR.accent[2], COLOR.accent[3], 1)
    arrow:SetSize(5, 8)
    arrow:SetPoint("LEFT", parts.hint, "LEFT", 0, 0)
    if arrow.SetVertexOffset then
        -- Pull the right-hand corners together: a triangle pointing right.
        arrow:SetVertexOffset(3, 0, -4)
        arrow:SetVertexOffset(4, 0, 4)
    end
    parts.hintText = Widgets.Text(parts.hint, "GameFontHighlightSmall", "", "LEFT")
    parts.hintText:SetPoint("LEFT", arrow, "RIGHT", 3, 0)
    parts.hintText:SetTextColor(COLOR.accent[1], COLOR.accent[2], COLOR.accent[3])
    parts.hint:Hide()

    parts.caption = Widgets.Text(frame, "GameFontNormalSmall", "", "RIGHT")
    parts.caption:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -33)
    parts.caption:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])

    -- Two rows of numbers.
    parts.steps = Widgets.Text(frame, "GameFontHighlight", "", "LEFT")
    parts.steps:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -66)
    parts.kcal = Widgets.Text(frame, "GameFontHighlight", "", "RIGHT")
    parts.kcal:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -66)
    parts.distance = Widgets.Text(frame, "GameFontHighlight", "", "LEFT")
    parts.distance:SetPoint("TOPLEFT", frame, "TOPLEFT", 10, -83)
    parts.time = Widgets.Text(frame, "GameFontHighlight", "", "RIGHT")
    parts.time:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -10, -83)

    -- Daily step goal.
    parts.barBack = Widgets.Fill(frame, "BORDER", 1, 1, 1, 0.10)
    parts.barBack:SetSize(BAR_WIDTH, 5)
    parts.barBack:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 9, 8)
    parts.barFill = Widgets.Fill(frame, "ARTWORK", COLOR.accent[1], COLOR.accent[2], COLOR.accent[3], 0.9)
    parts.barFill:SetSize(1, 5)
    parts.barFill:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 9, 8)
    parts.barFill:Hide()
end

----------------------------------------------------------------------------
-- Public API
----------------------------------------------------------------------------

--- Is the HUD on screen?
function HUD.IsShown()
    return frame ~= nil and frame:IsShown() and true or false
end

--- Show or hide the HUD and remember the choice.
function HUD.SetShown(shown)
    if not frame then
        return
    end
    shown = shown and true or false
    local wasShown = frame:IsShown()
    if Walka:Get("hud.shown") ~= shown then
        Walka:Set("hud.shown", shown)   -- fires "SETTINGS", which applies it
    else
        applySettings()
        update()
    end
    if wasShown and not shown and Walka:Get("announce") then
        Walka:Print("HUD hidden - type /walka to show it again.")
    end
end

function HUD.Toggle()
    HUD.SetShown(not HUD.IsShown())
end

----------------------------------------------------------------------------
-- Wiring
----------------------------------------------------------------------------

Walka:On("LOADED", function()
    create()
    applySettings()
    update()
end)

Walka:On("TICK", update)

Walka:On("SETTINGS", function()
    applySettings()
    update()
end)

Walka:On("HISTORY", function()
    cache.dirty = true
    update()
end)

-- Pausing and resuming do not change the totals; a session starting or
-- ending does. (A session that ends fires "HISTORY" first, which has then
-- already refreshed the totals - no need to merge the history twice.)
Walka:On("SESSION", function()
    if Walka:GetSession() ~= cache.session then
        cache.dirty = true
    end
    update()
end)

return HUD
