-- Walka options: a canvas panel in the game's Settings window (Esc ->
-- Options -> AddOns -> Walka).
--
-- The Settings window shows a "canvas" category by parenting our frame into
-- itself, so the controls are only built the first time the frame is shown.
-- Every control reads its value with Walka:Get and writes it with Walka:Set.
-- Walka:Set fires "SETTINGS", on which every control is set again from the
-- settings - so while that happens (`refreshing`) the controls' own handlers
-- must stay quiet, or a control would answer its own echo.
--
-- The pace is stored in km/h whatever the display unit; its slider works in
-- the display unit, in steps of 0.1.

local ADDON_NAME, ns = ...

local Walka = ns.Walka
local Widgets = ns.Widgets
local Metrics = ns.Metrics
local COLOR = Widgets.COLOR

local Options = {}
ns.Options = Options

local floor, max = math.floor, math.max

local KG_PER_LB = 0.45359237
local CM_PER_INCH = 2.54

local CONTENT_WIDTH = 610
local LEFT = 16                       -- left edge of labels and checkboxes
local LABEL_WIDTH = 190
local CONTROL_X = LEFT + LABEL_WIDTH + 8
local SCROLL_TOP = -58                -- below the fixed title
local WHEEL_STEP = 44

local panel               -- the canvas frame
local category            -- its Settings category
local built = false
local buildFailed = false
local refreshing = false  -- controls are being set from the settings
local controls = {}

----------------------------------------------------------------------------
-- Values
----------------------------------------------------------------------------

local function round(value, decimals)
    local factor = 10 ^ (decimals or 0)
    return floor(value * factor + 0.5) / factor
end

--- The pace slider's range in the display unit: what Walka takes for a
-- pace, on the 0.1 grid and rounded inwards, so that neither end of the
-- slider is a value that would be refused.
local function paceRange()
    local low = math.ceil(Walka:FromKmh(Metrics.MIN_PACE_KMH) * 10 - 1e-6) / 10
    local high = floor(Walka:FromKmh(Metrics.MAX_PACE_KMH) * 10 + 1e-6) / 10
    return low, high
end

local function formatSpeed(displayValue)
    return string.format("%.1f %s", displayValue, Walka:SpeedUnit())
end

local function formatPercent(value)
    return string.format("%d%%", floor(value * 100 + 0.5))
end

local function formatPixels(value)
    return string.format("%d px", value)
end

-- Weight and height, shown in lb and feet'inches with mph, kg and cm otherwise.

local function formatWeight(kg)
    if Walka:IsMph() then
        return string.format("%d", floor(kg / KG_PER_LB + 0.5))
    end
    if math.abs(kg - floor(kg + 0.5)) < 0.05 then
        return string.format("%d", floor(kg + 0.5))
    end
    return string.format("%.1f", kg)
end

local function formatHeight(cm)
    if Walka:IsMph() then
        local inches = floor(cm / CM_PER_INCH + 0.5)
        return string.format("%d'%d\"", floor(inches / 12), inches % 12)
    end
    return string.format("%d", floor(cm + 0.5))
end

-- "180", "180 lb", "82kg", "81,5" -> kilograms, or nil.
local function parseWeight(text)
    text = string.lower(string.gsub(text or "", ",", "."))
    local number, unit = string.match(text, "^%s*([%d%.]+)%s*(%a*)%s*$")
    number = tonumber(number)
    if not number then
        return nil
    end
    if unit == "" then
        unit = Walka:IsMph() and "lb" or "kg"
    end
    if unit == "lb" or unit == "lbs" then
        number = number * KG_PER_LB
    elseif unit ~= "kg" then
        return nil
    end
    if number < 30 or number > 250 then
        return nil
    end
    return round(number, 1)
end

-- "5'8", "5'8\"", "68", "68in", "172", "172cm", "1.72m" -> centimetres, or nil.
local function parseHeight(text)
    text = string.lower(string.gsub(text or "", ",", "."))
    local cm
    local feet, inches = string.match(text, "^%s*(%d+)%s*'%s*(%d*)")
    if feet then
        cm = (tonumber(feet) * 12 + (tonumber(inches) or 0)) * CM_PER_INCH
    else
        local number, unit = string.match(text, "^%s*([%d%.]+)%s*(%a*)%s*$")
        number = tonumber(number)
        if not number then
            return nil
        end
        if unit == "" then
            unit = Walka:IsMph() and "in" or "cm"
        end
        if unit == "in" or unit == "inch" or unit == "inches" then
            cm = number * CM_PER_INCH
        elseif unit == "cm" then
            cm = number
        elseif unit == "m" then
            cm = number * 100
        else
            return nil
        end
    end
    if cm < 120 or cm > 230 then
        return nil
    end
    return round(cm)
end

----------------------------------------------------------------------------
-- Refresh: settings -> controls
----------------------------------------------------------------------------

local function refreshLive()
    local text
    local session = Walka:GetSession()
    if not session then
        text = "No session is running: nothing is being counted. Start one with /walka start."
    elseif session.paused then
        text = "The session is paused: nothing is being counted."
    else
        text = string.format("Right now: counting your walk at %s.", Walka:FormatSpeed(Walka:GetLiveSpeed()))
    end
    controls.live:SetText(text)
end

local function refreshControls()
    local mph = Walka:IsMph()
    controls.units:SetValue(mph and "mph" or "kmh")

    -- Pace.
    local low, high = paceRange()
    controls.pace:Setup(low, high, 0.1)
    controls.pace:Display(Walka:FromKmh(Walka:GetPace()))
    refreshLive()

    -- Session.
    controls.autoPause:SetChecked(Walka:Get("autoPauseAFK") ~= false)
    controls.announce:SetChecked(Walka:Get("announce") ~= false)
    controls.goal:Display(string.format("%d", max(0, tonumber(Walka:Get("dailyStepGoal")) or 0)))

    -- Body. The companion's profile, when it has one, wins and is read-only.
    local profile = Walka:GetProfile()
    local fromCompanion = profile.fromCompanion == true
    controls.weight:Display(formatWeight(tonumber(profile.weightKg) or 75))
    controls.height:Display(formatHeight(tonumber(profile.heightCm) or 172))
    controls.weight:SetEnabled(not fromCompanion)
    controls.height:SetEnabled(not fromCompanion)
    local shade = fromCompanion and 0.6 or 1
    controls.weight:SetTextColor(shade, shade, shade)
    controls.height:SetTextColor(shade, shade, shade)
    controls.weightUnit:SetText(mph and "lb" or "kg")
    controls.heightUnit:SetText(mph and "feet ' inches" or "cm")
    controls.bodyNote:SetShown(fromCompanion)

    -- Beacon.
    controls.beacon:SetChecked(Walka:Get("beacon.enabled") ~= false)
    controls.cell:Setup(ns.Beacon.MIN_CELL_PX, ns.Beacon.MAX_CELL_PX, 1)
    controls.cell:Display(tonumber(Walka:Get("beacon.cellPx")) or ns.Beacon.DEFAULT_CELL_PX)

    -- HUD.
    controls.hudShown:SetChecked(Walka:Get("hud.shown") ~= false)
    controls.hudLocked:SetChecked(Walka:Get("hud.locked") == true)
    controls.hudScale:Setup(0.6, 1.6, 0.05)
    controls.hudScale:Display(tonumber(Walka:Get("hud.scale")) or 1)
    controls.hudAlpha:Setup(0.3, 1.0, 0.05)
    controls.hudAlpha:Display(tonumber(Walka:Get("hud.alpha")) or 0.9)

    -- Road.
    controls.recordRoad:SetChecked(Walka:Get("recordRoad") ~= false)
    controls.roadOnMap:SetChecked(Walka:Get("worldMap.road") ~= false)
    controls.roadButton:SetChecked(Walka:Get("worldMap.button") ~= false)

    -- Companion.
    local synced = Walka:GetSyncTime()
    if synced then
        controls.companion:SetText("Last synced " .. date("%b %d, %H:%M", synced) .. ".")
    else
        controls.companion:SetText("Not synced yet - run the companion app, then /reload.")
    end
end

local function refresh()
    if not built or refreshing then
        return
    end
    refreshing = true
    -- pcall so that `refreshing` can never be left switched on.
    local ok, err = pcall(refreshControls)
    refreshing = false
    if not ok then
        geterrorhandler()(err)
    end
end

----------------------------------------------------------------------------
-- Writes: controls -> settings
----------------------------------------------------------------------------

-- A handler for a control: ignores the control's own echo during a refresh.
local function userChange(handler)
    return function(...)
        if not refreshing then
            handler(...)
        end
    end
end

----------------------------------------------------------------------------
-- Construction
----------------------------------------------------------------------------

local function build()
    -- Fixed title above the scrolling part.
    local title = Widgets.Text(panel, "GameFontNormalLarge", "Walka", "LEFT")
    title:SetPoint("TOPLEFT", panel, "TOPLEFT", LEFT, -14)
    local version = C_AddOns and C_AddOns.GetAddOnMetadata and C_AddOns.GetAddOnMetadata(ADDON_NAME, "Version")
    if type(version) == "string" and version ~= "" then
        local versionText = Widgets.Text(panel, "GameFontDisableSmall", "v" .. version, "LEFT")
        versionText:SetPoint("BOTTOMLEFT", title, "BOTTOMRIGHT", 6, 1)
    end
    local subtitle = Widgets.Text(
        panel,
        "GameFontHighlightSmall",
        "Walka counts your walk while you play. The belt's speed is set at the treadmill, not here.",
        "LEFT"
    )
    subtitle:SetPoint("TOPLEFT", panel, "TOPLEFT", LEFT, -36)

    -- Scrolling part: a plain scroll frame, the mouse wheel and our own bar.
    local scroll = CreateFrame("ScrollFrame", nil, panel)
    scroll:SetPoint("TOPLEFT", panel, "TOPLEFT", 0, SCROLL_TOP)
    scroll:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -18, 6)
    local content = CreateFrame("Frame", nil, scroll)
    content:SetSize(CONTENT_WIDTH, 100)
    scroll:SetScrollChild(content)

    local bar
    local function scrollRange()
        return max(0, (content:GetHeight() or 0) - (scroll:GetHeight() or 0))
    end
    local function scrollTo(value)
        value = Widgets.Clamp(value or 0, 0, scrollRange())
        scroll:SetVerticalScroll(value)
        bar:SetValue(value)
    end
    local function updateScroll()
        local viewHeight, contentHeight = scroll:GetHeight() or 0, content:GetHeight() or 0
        bar:SetRange(scrollRange(), contentHeight > 0 and viewHeight / contentHeight or 1, viewHeight * 0.8)
        scrollTo(scroll:GetVerticalScroll())
    end
    bar = Widgets.ScrollBar(panel, function(value)
        scroll:SetVerticalScroll(value)
    end)
    bar:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -5, SCROLL_TOP)
    bar:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -5, 6)
    scroll:EnableMouseWheel(true)
    scroll:SetScript("OnMouseWheel", function(self, delta)
        scrollTo((self:GetVerticalScroll() or 0) - (delta or 0) * WHEEL_STEP)
    end)
    scroll:SetScript("OnSizeChanged", updateScroll)
    controls.updateScroll = updateScroll

    -- Layout: everything is placed top-down with a running y.
    local y = 0

    local function place(region, x, height, gap)
        region:SetPoint("TOPLEFT", content, "TOPLEFT", x, y)
        y = y - height - (gap or 0)
    end

    local function header(text)
        y = y - 10
        local heading = Widgets.Header(content, text)
        heading:SetPoint("TOPLEFT", content, "TOPLEFT", LEFT - 4, y)
        heading:SetWidth(CONTENT_WIDTH - 2 * (LEFT - 4))
        y = y - 24 - 8
    end

    -- Grey explanatory text; `lines` is how many lines it wraps to.
    local function note(text, lines, color)
        local fontString = Widgets.Text(content, "GameFontHighlightSmall", text, "LEFT")
        fontString:SetWidth(CONTENT_WIDTH - 2 * LEFT)
        fontString:SetJustifyV("TOP")
        color = color or COLOR.grey
        fontString:SetTextColor(color[1], color[2], color[3])
        fontString:SetHeight((lines or 1) * 12)
        place(fontString, LEFT, (lines or 1) * 12, 6)
        return fontString
    end

    -- Left-hand label of a row that starts at the current y.
    local function label(text, rowHeight)
        local fontString = Widgets.Text(content, "GameFontNormal", text, "LEFT")
        fontString:SetPoint("LEFT", content, "TOPLEFT", LEFT, y - rowHeight / 2)
        fontString:SetWidth(LABEL_WIDTH)
        return fontString
    end

    local function sliderRow(text, onChange, format)
        label(text, 28)
        local slider = Widgets.Slider(content, 250, userChange(onChange), format)
        place(slider, CONTROL_X, 28, 2)
        return slider
    end

    local function checkRow(text, onChange, x)
        local box = Widgets.Checkbox(content, text, userChange(onChange))
        -- The box's art has a few pixels of padding; line its edge up.
        place(box, (x or LEFT) - 3, 26, 0)
        return box
    end

    local function choiceRow(text, items, onSelect)
        label(text, 22)
        local choice = Widgets.Choice(content, items, userChange(onSelect))
        place(choice, CONTROL_X, 22, 8)
        return choice
    end

    local function boxRow(text, width, onCommit, suffix)
        label(text, 22)
        local box = Widgets.NumberBox(content, width, onCommit)
        -- The template's border reaches 5 px left of the box itself.
        box:SetPoint("TOPLEFT", content, "TOPLEFT", CONTROL_X + 5, y - 1)
        local unit = Widgets.Text(content, "GameFontHighlightSmall", suffix or "", "LEFT")
        unit:SetPoint("LEFT", box, "RIGHT", 8, 0)
        unit:SetTextColor(COLOR.grey[1], COLOR.grey[2], COLOR.grey[3])
        y = y - 22 - 6
        return box, unit
    end

    -- Units ---------------------------------------------------------------
    header("Units")
    controls.units = choiceRow("Speeds and distances in", {
        { value = "mph", text = "mph / miles", width = 96 },
        { value = "kmh", text = "km/h / km", width = 96 },
    }, function(value)
        Walka:Set("units", value)
    end)

    -- Your pace ---------------------------------------------------------------
    header("Your pace")
    note(
        "The speed your treadmill is set to. Walka cannot see the belt from inside the game, so its live "
            .. "numbers are counted with this pace. What the treadmill measured replaces them after /reload.",
        2
    )
    controls.pace = sliderRow("My treadmill is set to", function(value)
        Walka:SetPace(Walka:ToKmh(value))
    end, formatSpeed)
    controls.live = note("")

    -- Session ---------------------------------------------------------------
    header("Session")
    controls.autoPause = checkRow("Pause automatically when I go AFK", function(checked)
        Walka:Set("autoPauseAFK", checked)
    end)
    controls.announce = checkRow("Show session messages in chat", function(checked)
        Walka:Set("announce", checked)
    end)
    y = y - 4
    controls.goal = boxRow("Daily step goal", 70, function(text)
        -- Digits only, after dropping spaces and thousands separators.
        local digits = string.match((string.gsub(text or "", "[%s,%.]", "")), "^%d+$")
        local goal = digits and tonumber(digits)
        if goal and goal <= 200000 then
            Walka:Set("dailyStepGoal", goal)
        else
            Walka:Print("the daily step goal must be a number, for example 6000 (0 switches it off).")
        end
    end, "steps (0 switches the goal off)")

    -- Your body ---------------------------------------------------------------
    header("Your body")
    note("Used for the step and calorie estimates.")
    controls.weight, controls.weightUnit = boxRow("Weight", 70, function(text)
        local kg = parseWeight(text)
        if kg then
            Walka:Set("profile.weightKg", kg)
        else
            Walka:Print("that weight looks wrong - try 180lb or 82kg.")
        end
    end)
    controls.height, controls.heightUnit = boxRow("Height", 70, function(text)
        local cm = parseHeight(text)
        if cm then
            Walka:Set("profile.heightCm", cm)
        else
            Walka:Print("that height looks wrong - try 5'11 or 180cm.")
        end
    end)
    controls.bodyNote = note("Set in the companion app.", 1, COLOR.accent)

    -- Beacon ------------------------------------------------------------------
    header("Beacon")
    note(
        "The strip of small squares in the top-left corner of the game window is how the companion app "
            .. "reads the addon. Make the squares bigger if the companion app cannot read it.",
        2
    )
    controls.beacon = checkRow("Show the beacon (the companion app needs it)", function(checked)
        Walka:Set("beacon.enabled", checked)
    end)
    controls.cell = sliderRow("Square size", function(value)
        Walka:Set("beacon.cellPx", floor(value + 0.5))
    end, formatPixels)

    -- HUD -----------------------------------------------------------------------
    header("HUD")
    controls.hudShown = checkRow("Show the HUD", function(checked)
        Walka:Set("hud.shown", checked)
    end)
    controls.hudLocked = checkRow("Lock its position", function(checked)
        Walka:Set("hud.locked", checked)
    end)
    controls.hudScale = sliderRow("Size", function(value)
        Walka:Set("hud.scale", value)
    end, formatPercent)
    controls.hudAlpha = sliderRow("Opacity", function(value)
        Walka:Set("hud.alpha", value)
    end, formatPercent)

    -- Your road ---------------------------------------------------------------
    header("Your road")
    note(
        "Walka keeps the path your character travels and draws it on the world map (on OpenMap's map too, "
            .. "if you use OpenMap): coloured by level, bright where you walked it on the treadmill.",
        2
    )
    controls.recordRoad = checkRow("Record my character's road", function(checked)
        Walka:Set("recordRoad", checked)
        if ns.RoadRecorder then
            ns.RoadRecorder.SetEnabled(checked)
        end
    end)
    controls.roadOnMap = checkRow("Draw it on the world map", function(checked)
        Walka:Set("worldMap.road", checked)
    end)
    controls.roadButton = checkRow("Show the Walka road button on the world map", function(checked)
        Walka:Set("worldMap.button", checked)
    end)

    -- Companion app ---------------------------------------------------------------
    header("Companion app")
    controls.companion = note("")

    content:SetHeight(-y + 16)
    updateScroll()
end

----------------------------------------------------------------------------
-- The canvas frame and its registration
----------------------------------------------------------------------------

local function onShow()
    if not built and not buildFailed then
        local ok, err = pcall(build)
        if ok then
            built = true
        else
            -- Leave a readable page rather than a half-built one that would
            -- throw again on every refresh.
            buildFailed = true
            local sorry = Widgets.Text(
                panel,
                "GameFontHighlight",
                "Walka could not build its options page. The slash commands still work: type /walka help.",
                "LEFT"
            )
            sorry:SetPoint("TOPLEFT", panel, "TOPLEFT", LEFT, -80)
            sorry:SetWidth(CONTENT_WIDTH - 2 * LEFT)
            geterrorhandler()(err)
        end
    end
    if built then
        controls.updateScroll()
        refresh()
    end
end

local function register()
    if category then
        return
    end
    if not (Settings and Settings.RegisterCanvasLayoutCategory and Settings.RegisterAddOnCategory) then
        return
    end
    panel = CreateFrame("Frame", "WalkaOptionsPanel")
    panel:Hide()
    panel:SetScript("OnShow", onShow)
    -- The Settings window calls this whenever it opens.
    function panel:OnRefresh()
        if built and self:IsVisible() then
            refresh()
        end
    end
    category = Settings.RegisterCanvasLayoutCategory(panel, "Walka")
    Settings.RegisterAddOnCategory(category)
end

----------------------------------------------------------------------------
-- Public API
----------------------------------------------------------------------------

--- Open the Settings window on Walka's page.
function Options.Open()
    if not category then
        Walka:Print("the options page is not available; the slash commands still work (/walka help).")
        return
    end
    -- The Settings window cannot be opened by an addon during combat.
    if InCombatLockdown and InCombatLockdown() then
        Walka:Print("the options cannot be opened in combat - try again after the fight.")
        return
    end
    local ok = pcall(Settings.OpenToCategory, category:GetID())
    if not ok then
        Walka:Print("the options could not be opened just now - try Esc > Options > AddOns > Walka.")
    end
end

----------------------------------------------------------------------------
-- Wiring
----------------------------------------------------------------------------

Walka:On("LOADED", register)

Walka:On("SETTINGS", function()
    if built and panel:IsVisible() then
        refresh()
    end
end)

Walka:On("SESSION", function()
    if built and panel:IsVisible() then
        refreshLive()
    end
end)

Walka:On("TICK", function()
    if built and panel:IsVisible() then
        refreshLive()
    end
end)

return Options
