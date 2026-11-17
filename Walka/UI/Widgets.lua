-- Walka UI widgets: the few controls the HUD, the history window and the
-- options panel are built from.
--
-- Everything here is deliberately plain so that it keeps working when the
-- client changes: basic frames, solid-colour textures (SetColorTexture needs
-- no art file), the game's standard font objects, and a handful of stock
-- templates used the way Blizzard's own UI uses them:
--
--   BackdropTemplate                    - panel border (SetBackdrop)
--   UIPanelButtonTemplate               - text button
--   UICheckButtonTemplate               - checkbox (its label is .Text)
--   InputBoxTemplate                    - edit box
--   UIPanelCloseButtonNoScripts         - close button art
--   MinimalSliderWithSteppersTemplate   - slider; driven through its mixin
--                                         (Init + RegisterCallback), not
--                                         through a plain OnValueChanged
--
-- None of these frames is protected, so all of it is safe in combat.

local ADDON_NAME, ns = ...

local Widgets = {}
ns.Widgets = Widgets

local floor, max, min = math.floor, math.max, math.min

----------------------------------------------------------------------------
-- Colours and small helpers
----------------------------------------------------------------------------

--- The game's usual text colours as plain numbers, plus Walka's accent
-- (the blue of the chat prefix).
local COLOR = {
    gold = { 1.00, 0.82, 0.00 },
    white = { 1.00, 1.00, 1.00 },
    grey = { 0.62, 0.62, 0.62 },
    dark = { 0.38, 0.38, 0.38 },
    green = { 0.30, 0.85, 0.40 },
    amber = { 1.00, 0.70, 0.20 },
    red = { 1.00, 0.30, 0.30 },
    accent = { 0.31, 0.76, 0.97 },
}
Widgets.COLOR = COLOR

--- Wrap text in a colour escape: Widgets.Colorize("12", COLOR.grey).
function Widgets.Colorize(text, color)
    return string.format(
        "|cff%02x%02x%02x%s|r",
        floor(color[1] * 255 + 0.5),
        floor(color[2] * 255 + 0.5),
        floor(color[3] * 255 + 0.5),
        tostring(text)
    )
end

--- `value` limited to low..high. Not-a-number (which a saved setting or a
-- typed "nan" can turn into) becomes `low`: no widget accepts NaN.
function Widgets.Clamp(value, low, high)
    if value ~= value or value < low then
        return low
    elseif value > high then
        return high
    end
    return value
end

--- The click every stock control makes.
function Widgets.ClickSound(off)
    if PlaySound and SOUNDKIT then
        local kit = off and SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_OFF or SOUNDKIT.IG_MAINMENU_OPTION_CHECKBOX_ON
        if kit then
            PlaySound(kit)
        end
    end
end

-- Add a script handler without throwing away one a template installed.
local function hook(frame, script, handler)
    if frame:GetScript(script) then
        frame:HookScript(script, handler)
    else
        frame:SetScript(script, handler)
    end
end
Widgets.Hook = hook

--- A font string. `font` is the name of a font object (default
-- GameFontHighlight); `justify` is "LEFT", "CENTER" or "RIGHT".
function Widgets.Text(parent, font, text, justify)
    local fontString = parent:CreateFontString(nil, "ARTWORK", font or "GameFontHighlight")
    if justify then
        fontString:SetJustifyH(justify)
    end
    if text then
        fontString:SetText(text)
    end
    return fontString
end

--- A solid-colour texture.
function Widgets.Fill(parent, layer, r, g, b, a)
    local texture = parent:CreateTexture(nil, layer or "ARTWORK")
    texture:SetColorTexture(r, g, b, a or 1)
    return texture
end

--- A small round dot, built from three overlapping rectangles (a plus
-- sign with its corners filled in) so that it needs no art. Returns a frame
-- with :SetColor(r, g, b).
function Widgets.Dot(parent, size, color)
    local dot = CreateFrame("Frame", nil, parent)
    dot:SetSize(size, size)
    local pieces = {}
    for index, shape in ipairs({ { 1, 3 / 7 }, { 5 / 7, 5 / 7 }, { 3 / 7, 1 } }) do
        local piece = Widgets.Fill(dot, "ARTWORK", 1, 1, 1, 1)
        piece:SetSize(size * shape[1], size * shape[2])
        piece:SetPoint("CENTER", dot, "CENTER", 0, 0)
        pieces[index] = piece
    end

    function dot:SetColor(r, g, b)
        for index = 1, #pieces do
            pieces[index]:SetVertexColor(r, g, b, 1)
        end
    end

    if color then
        dot:SetColor(color[1], color[2], color[3])
    end
    return dot
end

----------------------------------------------------------------------------
-- Tooltips
----------------------------------------------------------------------------

--- Show the game tooltip next to `owner`: a white title and, below it,
-- `body` - one string or a list of strings - in the usual gold, wrapped.
-- An empty string in the list makes a blank line.
function Widgets.ShowTooltip(owner, title, body, anchor)
    local tooltip = GameTooltip
    if not tooltip or not title then
        return
    end
    tooltip:SetOwner(owner, anchor or "ANCHOR_RIGHT")
    tooltip:ClearLines()
    tooltip:AddLine(title, 1, 1, 1)
    local gold = COLOR.gold
    if type(body) == "table" then
        for index = 1, #body do
            local line = body[index]
            tooltip:AddLine(line ~= "" and line or " ", gold[1], gold[2], gold[3], true)
        end
    elseif body then
        tooltip:AddLine(body, gold[1], gold[2], gold[3], true)
    end
    tooltip:Show()
end

function Widgets.HideTooltip(owner)
    local tooltip = GameTooltip
    if tooltip and tooltip:GetOwner() == owner then
        tooltip:Hide()
    end
end

--- Give a frame a tooltip. `title` and `body` may be functions, called each
-- time the tooltip opens, so it can describe the current state; `anchor`
-- may be a function too. The frame must receive the mouse (buttons do).
function Widgets.Tooltip(frame, title, body, anchor)
    -- A function may return nil ("nothing to say right now"), so this cannot
    -- be the `f and f() or f` idiom: that would fall back to the function.
    local function resolve(value, self)
        if type(value) == "function" then
            return value(self)
        end
        return value
    end
    hook(frame, "OnEnter", function(self)
        -- No title, no tooltip.
        Widgets.ShowTooltip(self, resolve(title, self), resolve(body, self), resolve(anchor, self))
    end)
    hook(frame, "OnLeave", function(self)
        Widgets.HideTooltip(self)
    end)
end

----------------------------------------------------------------------------
-- Panel
----------------------------------------------------------------------------

-- The classic tooltip border. Only the border comes from an art file; the
-- background is a colour of our own so that its opacity is ours to choose.
local PANEL_BORDER = {
    edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
    tileEdge = true,
    edgeSize = 14,
}

--- A bordered, dark panel. `name` is optional (a global frame name).
function Widgets.Panel(parent, name, fillAlpha)
    local panel = CreateFrame("Frame", name, parent, "BackdropTemplate")
    local fill = Widgets.Fill(panel, "BACKGROUND", 0.03, 0.03, 0.05, fillAlpha or 0.85)
    fill:SetPoint("TOPLEFT", panel, "TOPLEFT", 3, -3)
    fill:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -3, 3)
    panel.Fill = fill
    panel:SetBackdrop(PANEL_BORDER)
    panel:SetBackdropBorderColor(0.55, 0.55, 0.60, 1)
    return panel
end

--- Let the user drag a frame with the left button. `canMove()` (optional)
-- can veto a drag; `onMoved(frame)` (optional) runs when one ends.
function Widgets.MakeMovable(frame, canMove, onMoved)
    frame:SetMovable(true)
    frame:SetClampedToScreen(true)
    -- We place our frames ourselves; keep the client's layout cache out of it.
    frame:SetDontSavePosition(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")

    local function stop(self)
        if self.walkaMoving then
            self.walkaMoving = nil
            self:StopMovingOrSizing()
            if onMoved then
                onMoved(self)
            end
        end
    end

    frame:SetScript("OnDragStart", function(self)
        if not canMove or canMove(self) then
            self.walkaMoving = true
            self:StartMoving()
        end
    end)
    frame:SetScript("OnDragStop", stop)
    -- A frame hidden in mid-drag would otherwise stay glued to the cursor.
    hook(frame, "OnHide", stop)
end

----------------------------------------------------------------------------
-- Buttons
----------------------------------------------------------------------------

--- A standard red-and-gold text button.
function Widgets.Button(parent, text, width, onClick)
    local button = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    button:SetSize(width or 120, 22)
    button:SetText(text)
    button:SetScript("OnClick", function(self, mouseButton)
        Widgets.ClickSound()
        if onClick then
            onClick(self, mouseButton)
        end
    end)
    return button
end

--- The standard close cross, in the top-right corner of `parent`.
function Widgets.CloseButton(parent, onClick)
    local button = CreateFrame("Button", nil, parent, "UIPanelCloseButtonNoScripts")
    button:SetPoint("TOPRIGHT", parent, "TOPRIGHT", 0, 0)
    button:SetScript("OnClick", function(self)
        Widgets.ClickSound(true)
        if onClick then
            onClick(self)
        end
    end)
    return button
end

-- Glyphs for the small icon buttons, drawn from solid rectangles so that
-- they need no art and all share one style. Each entry is
-- { centreX, centreY, width, height } in a 16-unit box centred on the
-- button. "play" is one rectangle whose right-hand corners are pulled
-- together into a point.
local GLYPHS = {
    play = { { 0.5, 0, 9, 10, point = true } },
    pause = { { -2.5, 0, 3, 10 }, { 2.5, 0, 3, 10 } },
    stop = { { 0, 0, 9, 9 } },
    -- three rising bars: "history / statistics"
    chart = { { -3.5, -3, 2.5, 4 }, { 0, -1.5, 2.5, 7 }, { 3.5, 0, 2.5, 10 } },
    -- two sliders: "options"
    sliders = { { 0, 2.75, 10, 1.5 }, { 2, 2.75, 2.5, 4.5 }, { 0, -2.75, 10, 1.5 }, { -2, -2.75, 2.5, 4.5 } },
}

-- Corner indices for Texture:SetVertexOffset.
local UPPER_RIGHT, LOWER_RIGHT = 3, 4

local function drawGlyph(holder, spec, unit)
    local parts = {}
    for index = 1, #spec do
        local rect = spec[index]
        local texture = Widgets.Fill(holder, "ARTWORK", 1, 1, 1, 1)
        texture:SetSize(rect[3] * unit, rect[4] * unit)
        texture:SetPoint("CENTER", holder, "CENTER", rect[1] * unit, rect[2] * unit)
        if rect.point and texture.SetVertexOffset then
            local half = rect[4] * unit / 2
            texture:SetVertexOffset(UPPER_RIGHT, 0, -half)
            texture:SetVertexOffset(LOWER_RIGHT, 0, half)
        end
        parts[index] = texture
    end
    return parts
end

--- A small square icon button. `glyph` is one of "play", "pause", "stop",
-- "chart", "sliders". Returns a Button with two extra methods:
--   button:SetGlyph(name)      - swap the icon
--   button:SetActive(enabled)  - enable/disable (greyed out when disabled)
function Widgets.GlyphButton(parent, size, glyph, onClick)
    size = size or 16
    local button = CreateFrame("Button", nil, parent)
    button:SetSize(size, size)
    -- Keep OnEnter/OnLeave while disabled so the tooltip still explains it.
    button:SetMotionScriptsWhileDisabled(true)

    local back = Widgets.Fill(button, "BACKGROUND", 1, 1, 1, 0.07)
    back:SetAllPoints(button)

    -- The glyph sits in its own frame so that it can be nudged while the
    -- button is held down.
    local holder = CreateFrame("Frame", nil, button)
    holder:SetSize(size, size)
    holder:SetPoint("CENTER", button, "CENTER", 0, 0)

    local glyphs = {}
    local current
    local hovered = false

    local function recolor()
        local parts = glyphs[current]
        if not parts then
            return
        end
        local color, alpha = COLOR.gold, 0.9
        if not button:IsEnabled() then
            color, alpha = COLOR.grey, 0.35
        elseif hovered then
            color, alpha = COLOR.white, 1
        end
        for index = 1, #parts do
            parts[index]:SetVertexColor(color[1], color[2], color[3], alpha)
        end
        back:SetColorTexture(1, 1, 1, (hovered and button:IsEnabled()) and 0.18 or 0.07)
    end

    function button:SetGlyph(name)
        if not GLYPHS[name] or name == current then
            return
        end
        if glyphs[current] then
            for _, texture in ipairs(glyphs[current]) do
                texture:Hide()
            end
        end
        if not glyphs[name] then
            glyphs[name] = drawGlyph(holder, GLYPHS[name], size / 16)
        end
        for _, texture in ipairs(glyphs[name]) do
            texture:Show()
        end
        current = name
        recolor()
    end

    function button:SetActive(enabled)
        self:SetEnabled(enabled and true or false)
        recolor()
    end

    button:SetScript("OnEnter", function()
        hovered = true
        recolor()
    end)
    button:SetScript("OnLeave", function()
        hovered = false
        recolor()
    end)
    button:SetScript("OnMouseDown", function(self)
        if self:IsEnabled() then
            holder:SetPoint("CENTER", self, "CENTER", 1, -1)
        end
    end)
    button:SetScript("OnMouseUp", function(self)
        holder:SetPoint("CENTER", self, "CENTER", 0, 0)
    end)
    button:SetScript("OnClick", function(self, mouseButton)
        Widgets.ClickSound()
        if onClick then
            onClick(self, mouseButton)
        end
    end)

    button:SetGlyph(glyph or "play")
    return button
end

----------------------------------------------------------------------------
-- Checkbox
----------------------------------------------------------------------------

--- A checkbox with a label to its right. `onChange(checked)` runs when the
-- user clicks it (not when SetChecked is called from code).
function Widgets.Checkbox(parent, label, onChange)
    local box = CreateFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
    box:SetSize(26, 26)
    local text = box.Text
    text:SetFontObject("GameFontHighlight")
    text:SetText(label)
    text:ClearAllPoints()
    text:SetPoint("LEFT", box, "RIGHT", 2, 0)
    -- Let a click on the label count as a click on the box.
    box:SetHitRectInsets(0, -(text:GetStringWidth() + 6), 0, 0)
    box:SetScript("OnClick", function(self)
        local checked = self:GetChecked() and true or false
        Widgets.ClickSound(not checked)
        if onChange then
            onChange(checked)
        end
    end)
    return box
end

----------------------------------------------------------------------------
-- Choice (a row of buttons of which one is selected)
----------------------------------------------------------------------------

--- A segmented control. `items` is a list of
-- { value = ..., text = "...", width = number, tooltip = "..." }.
-- `onSelect(value)` runs when the user picks a different item. Returns a
-- frame with :SetValue(value) (nil or an unknown value selects nothing)
-- and :GetValue().
function Widgets.Choice(parent, items, onSelect)
    local group = CreateFrame("Frame", nil, parent)
    local buttons = {}
    local selected

    local function paint(button)
        local isSelected = button.value == selected
        local accent = COLOR.accent
        if isSelected then
            button.Back:SetColorTexture(accent[1], accent[2], accent[3], 0.32)
            button.Label:SetTextColor(1, 1, 1)
        else
            button.Back:SetColorTexture(1, 1, 1, button.hovered and 0.14 or 0.06)
            button.Label:SetTextColor(0.75, 0.75, 0.75)
        end
        button.Mark:SetShown(isSelected)
    end

    local x = 0
    for index, item in ipairs(items) do
        local button = CreateFrame("Button", nil, group)
        button.value = item.value
        button.Back = Widgets.Fill(button, "BACKGROUND", 1, 1, 1, 0.06)
        button.Back:SetAllPoints(button)
        -- A line under the selected item.
        button.Mark = Widgets.Fill(button, "ARTWORK", COLOR.accent[1], COLOR.accent[2], COLOR.accent[3], 1)
        button.Mark:SetHeight(2)
        button.Mark:SetPoint("BOTTOMLEFT", button, "BOTTOMLEFT", 0, 0)
        button.Mark:SetPoint("BOTTOMRIGHT", button, "BOTTOMRIGHT", 0, 0)
        button.Label = Widgets.Text(button, "GameFontHighlightSmall", item.text)
        button.Label:SetPoint("CENTER", button, "CENTER", 0, 1)

        local width = item.width or max(60, button.Label:GetStringWidth() + 24)
        button:SetSize(width, 22)
        button:SetPoint("LEFT", group, "LEFT", x, 0)
        x = x + width + 3

        button:SetScript("OnEnter", function(self)
            self.hovered = true
            paint(self)
        end)
        button:SetScript("OnLeave", function(self)
            self.hovered = false
            paint(self)
        end)
        button:SetScript("OnClick", function(self)
            Widgets.ClickSound()
            if self.value ~= selected and onSelect then
                onSelect(self.value)
            end
        end)
        if item.tooltip then
            Widgets.Tooltip(button, item.text, item.tooltip)
        end
        buttons[index] = button
    end
    group:SetSize(max(1, x - 3), 22)

    function group:SetValue(value)
        selected = value
        for index = 1, #buttons do
            paint(buttons[index])
        end
    end

    function group:GetValue()
        return selected
    end

    group:SetValue(nil)
    return group
end

----------------------------------------------------------------------------
-- Slider
----------------------------------------------------------------------------

--- A slider with stepper arrows and the value shown to its right.
--
--   local slider = Widgets.Slider(parent, 250, onChange, format)
--   slider:Setup(minValue, maxValue, step)   -- may be called again later
--   slider:Display(value)                    -- show a value, no onChange
--   slider:SetEnabled(enabled)
--
-- `onChange(value)` runs only when the user moves the slider, with the
-- value snapped to the step grid; `format(value)` (optional) returns the
-- text for the value label.
function Widgets.Slider(parent, width, onChange, format)
    local control = CreateFrame("Frame", nil, parent, "MinimalSliderWithSteppersTemplate")
    control:SetSize(width or 250, 28)

    local mixin = MinimalSliderWithSteppersMixin
    local low, high, step = 0, 1, 1
    local last       -- last value shown or reported, to drop repeats
    local silent = false

    local function snap(value)
        value = low + floor((value - low) / step + 0.5) * step
        value = Widgets.Clamp(value, low, high)
        -- Drop floating-point dust such as 2.0000000001.
        return floor(value * 10000 + 0.5) / 10000
    end

    local formatters = {
        [mixin.Label.Right] = function(value)
            value = snap(value)
            if format then
                return format(value)
            end
            return tostring(value)
        end,
    }

    control:RegisterCallback(mixin.Event.OnValueChanged, function(_, value)
        if silent or not value then
            return
        end
        value = snap(value)
        if value ~= last then
            last = value
            if onChange then
                onChange(value)
            end
        end
    end, control)

    --- Set the range and step. Does nothing if they are unchanged, so it is
    -- cheap to call on every refresh.
    function control:Setup(minValue, maxValue, valueStep)
        if maxValue <= minValue then
            maxValue = minValue + valueStep
        end
        if self.walkaReady and minValue == low and maxValue == high and valueStep == step then
            return
        end
        low, high, step = minValue, maxValue, valueStep
        local steps = max(1, floor((high - low) / step + 0.5))
        local value = snap(last or low)
        last = value
        -- Init moves the thumb, which fires the callback: keep that quiet.
        silent = true
        self:Init(value, low, high, steps, formatters)
        silent = false
        self.walkaReady = true
    end

    --- Show `value` (clamped and snapped) without reporting a change.
    function control:Display(value)
        if not self.walkaReady then
            return
        end
        value = snap(value)
        last = value
        silent = true
        self:SetValue(value)
        -- The label is only rewritten when the thumb moves; the text may
        -- have changed for another reason (the unit, for instance).
        self:FormatValue(value)
        silent = false
    end

    return control
end

----------------------------------------------------------------------------
-- Edit box
----------------------------------------------------------------------------

--- A single-line box for typing a number.
--
--   local box = Widgets.NumberBox(parent, 70, onCommit)
--   box:Display("82")     -- show the stored value (ignored while typing)
--
-- `onCommit(text)` runs when the user presses Enter or clicks elsewhere
-- after changing the text. The box goes back to the last displayed value
-- first, so `onCommit` only has to store what it accepts and Display it.
-- Escape abandons the edit.
function Widgets.NumberBox(parent, width, onCommit)
    local box = CreateFrame("EditBox", nil, parent, "InputBoxTemplate")
    box:SetSize(width or 70, 20)
    box:SetAutoFocus(false)
    box:SetMaxLetters(12)
    box.walkaShown = ""

    function box:Display(text)
        self.walkaShown = tostring(text or "")
        if not self.walkaEditing then
            self:SetText(self.walkaShown)
            self:SetCursorPosition(0)
        end
    end

    box:SetScript("OnEnterPressed", function(self)
        self:ClearFocus()
    end)
    box:SetScript("OnEscapePressed", function(self)
        self.walkaCancelled = true
        self:ClearFocus()
    end)
    hook(box, "OnEditFocusGained", function(self)
        self.walkaEditing = true
    end)
    hook(box, "OnEditFocusLost", function(self)
        local text = self:GetText() or ""
        local cancelled = self.walkaCancelled
        self.walkaCancelled = nil
        self.walkaEditing = nil
        self:SetText(self.walkaShown)
        if not cancelled and text ~= self.walkaShown and onCommit then
            onCommit(text)
        end
    end)
    return box
end

----------------------------------------------------------------------------
-- Section header
----------------------------------------------------------------------------

--- A gold heading with a thin rule under it. Returns a frame 24 high; give
-- it a left and a right anchor (or a width).
function Widgets.Header(parent, text)
    local header = CreateFrame("Frame", nil, parent)
    header:SetHeight(24)
    header.Label = Widgets.Text(header, "GameFontNormalLarge", text, "LEFT")
    header.Label:SetPoint("BOTTOMLEFT", header, "BOTTOMLEFT", 0, 5)
    local rule = Widgets.Fill(header, "ARTWORK", COLOR.gold[1], COLOR.gold[2], COLOR.gold[3], 0.25)
    rule:SetHeight(1)
    rule:SetPoint("BOTTOMLEFT", header, "BOTTOMLEFT", 0, 0)
    rule:SetPoint("BOTTOMRIGHT", header, "BOTTOMRIGHT", 0, 0)
    return header
end

----------------------------------------------------------------------------
-- Scroll bar
----------------------------------------------------------------------------

--- A slim vertical scroll bar: a track with a thumb that can be dragged;
-- clicking the track pages up or down. It knows nothing about what it
-- scrolls:
--
--   local bar = Widgets.ScrollBar(parent, onScroll)
--   bar:SetRange(maxValue, visibleFraction, page)
--   bar:SetValue(value)          -- move the thumb, no onScroll
--
-- `onScroll(value)` runs when the user moves the thumb, with a value in
-- 0..maxValue. The bar hides itself when there is nothing to scroll.
function Widgets.ScrollBar(parent, onScroll)
    local bar = CreateFrame("Frame", nil, parent)
    bar:SetWidth(8)
    bar:EnableMouse(true)
    local track = Widgets.Fill(bar, "BACKGROUND", 1, 1, 1, 0.06)
    track:SetAllPoints(bar)

    local thumb = CreateFrame("Button", nil, bar)
    thumb:SetWidth(8)
    local thumbFill = Widgets.Fill(thumb, "ARTWORK", COLOR.gold[1], COLOR.gold[2], COLOR.gold[3], 0.45)
    thumbFill:SetAllPoints(thumb)

    local maxValue, value, fraction, page = 0, 0, 1, 1
    local travel = 0     -- how far the thumb can move, in UI units
    local dragCursor, dragValue

    local function layout()
        local height = bar:GetHeight() or 0
        local thumbHeight = Widgets.Clamp(height * fraction, min(18, height), max(height, 1))
        travel = max(0, height - thumbHeight)
        local offset = maxValue > 0 and travel * (value / maxValue) or 0
        thumb:SetHeight(max(1, thumbHeight))
        thumb:ClearAllPoints()
        thumb:SetPoint("TOP", bar, "TOP", 0, -offset)
    end

    local function cursorY()
        local _, y = GetCursorPosition()
        local scale = bar:GetEffectiveScale()
        if not scale or scale <= 0 then
            scale = 1
        end
        return (y or 0) / scale
    end

    local function report(newValue)
        newValue = Widgets.Clamp(newValue, 0, maxValue)
        if newValue ~= value then
            value = newValue
            layout()
            if onScroll then
                onScroll(value)
            end
        end
    end

    local function stopDrag()
        dragCursor = nil
        thumb:SetScript("OnUpdate", nil)
    end

    thumb:SetScript("OnMouseDown", function(self, mouseButton)
        if mouseButton ~= "LeftButton" then
            return
        end
        dragCursor, dragValue = cursorY(), value
        self:SetScript("OnUpdate", function()
            if dragCursor and travel > 0 then
                -- Moving the mouse down (smaller y) scrolls forward.
                report(dragValue + (dragCursor - cursorY()) / travel * maxValue)
            end
        end)
    end)
    thumb:SetScript("OnMouseUp", stopDrag)
    thumb:SetScript("OnHide", stopDrag)
    thumb:SetScript("OnEnter", function()
        thumbFill:SetColorTexture(COLOR.gold[1], COLOR.gold[2], COLOR.gold[3], 0.75)
    end)
    thumb:SetScript("OnLeave", function()
        thumbFill:SetColorTexture(COLOR.gold[1], COLOR.gold[2], COLOR.gold[3], 0.45)
    end)

    -- A click on the track, above or below the thumb, moves one page.
    bar:SetScript("OnMouseDown", function(_, mouseButton)
        if mouseButton ~= "LeftButton" or maxValue <= 0 then
            return
        end
        local thumbTop = thumb:GetTop()
        if thumbTop and cursorY() > thumbTop then
            report(value - page)
        else
            report(value + page)
        end
    end)
    bar:SetScript("OnSizeChanged", layout)

    function bar:SetRange(newMax, visibleFraction, newPage)
        maxValue = max(0, newMax or 0)
        fraction = Widgets.Clamp(visibleFraction or 1, 0.05, 1)
        page = newPage or max(1, maxValue * fraction)
        value = Widgets.Clamp(value, 0, maxValue)
        self:SetShown(maxValue > 0)
        layout()
    end

    function bar:SetValue(newValue)
        value = Widgets.Clamp(newValue or 0, 0, maxValue)
        layout()
    end

    function bar:GetValue()
        return value
    end

    bar:SetRange(0, 1)
    return bar
end

return Widgets
