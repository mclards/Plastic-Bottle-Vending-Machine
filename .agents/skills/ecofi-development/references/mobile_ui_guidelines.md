# VMC ECO-VENDO Mobile UI & Responsive Design Guidelines
> **Target Devices:** iPhone 17 Pro Max (~430px), Modern Large Mobile Viewports (390px - 430px), and Compact Phones (360px - 375px).

---

## 1. Monolithic HTML Variable Escaping (`PORTAL_HTML` & `ADMIN_HTML`)
- Both `PORTAL_HTML` and `ADMIN_HTML` in `host/portal.py` are embedded as massive single-line or multiline Python string literals with escaped newlines (`\n`).
- **Single Quote JavaScript Crash Hazard:**
  - Standard HTML attributes inside Python single-quoted strings must NEVER contain raw unescaped single quotes `'` inside inline JS (e.g. `onclick="if(confirm('Delete?'))"`).
  - Unescaped quotes break the browser's JavaScript parser, silently killing modals, event handlers, and tab switches.
  - **Always escape quotes as `&quot;` or `\'`**, and validate JavaScript syntax using `tools/check_js_syntax.py` (`node --check`) after any HTML edit.

---

## 2. Card Header vs Sub-Tab Preservation (`.card-header.p-0`)
- **The Pitfall:** In Bootstrap/AdminLTE, `.card-header` is often styled globally with flexbox and padding. Applying `display: flex !important` and `padding: 0.65rem 0.85rem !important` to `.card-header` breaks `<div class="card-header p-0 border-bottom-0">` used for sub-navigation tabs (`#network-tabs`, `#bandwidth-tabs`, `#system-tabs`).
- **The Rule:**
  - Always style `.card-header:not(.p-0)` for title, padding, and tool alignment.
  - Explicitly preserve tab wrappers:
    ```css
    .card-header.p-0 {
      padding: 0 !important;
      display: block !important;
      background: transparent !important;
    }
    ```
  - Card tools on mobile should expand across full width to create native iOS-style segmented controls:
    ```css
    .card-header:not(.p-0) .card-tools {
      margin-left: 0 !important;
      width: 100% !important;
      display: flex !important;
      align-items: center !important;
      justify-content: flex-start !important;
      flex-wrap: wrap !important;
      gap: 6px !important;
    }
    .card-header:not(.p-0) .card-tools .btn-group {
      width: 100% !important;
      display: flex !important;
    }
    .card-header:not(.p-0) .card-tools .btn-group .btn {
      flex: 1 1 0 !important;
      text-align: center !important;
    }
    ```

---

## 3. Touch-Scrollable Tabs & Pills
- Nav tabs and pills must never wrap awkwardly into multi-line stepped rows on mobile.
- Use fluid horizontal touch scrolling:
  ```css
  .nav-tabs, .nav-pills:not(.nav-sidebar) {
    display: flex !important;
    flex-wrap: nowrap !important;
    overflow-x: auto !important;
    overflow-y: hidden !important;
    -webkit-overflow-scrolling: touch !important;
    scrollbar-width: none !important;
    -ms-overflow-style: none !important;
    padding: 4px 6px !important;
    margin-bottom: 8px !important;
    gap: 6px !important;
    border-bottom: 1px solid var(--eco-border) !important;
  }
  .nav-tabs::-webkit-scrollbar, .nav-pills:not(.nav-sidebar)::-webkit-scrollbar {
    display: none !important;
  }
  .nav-tabs .nav-item, .nav-pills:not(.nav-sidebar) .nav-item {
    flex-shrink: 0 !important;
  }
  .nav-tabs .nav-link, .nav-pills:not(.nav-sidebar) .nav-link {
    white-space: nowrap !important;
    padding: 6px 12px !important;
    font-size: 0.82rem !important;
    border-radius: 6px !important;
  }
  ```

---

## 4. Dashboard Stat Cards (`.small-box`)
- On small screens, `.col-6` grids provide ~200px width per card.
- Apply responsive typography and watermark icon layering:
  ```css
  .small-box {
    margin-bottom: 10px !important;
    border-radius: 10px !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: space-between !important;
    min-height: 84px !important;
    position: relative !important;
    overflow: hidden !important;
  }
  .small-box .inner {
    padding: 10px 10px 8px 10px !important;
    position: relative !important;
    z-index: 2 !important;
  }
  .small-box .inner h3 {
    font-size: clamp(1.15rem, 4.5vw, 1.45rem) !important;
    font-weight: 700 !important;
    margin-bottom: 2px !important;
    line-height: 1.15 !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
  }
  .small-box .inner p {
    font-size: 0.72rem !important;
    margin-bottom: 0 !important;
    line-height: 1.2 !important;
    opacity: 0.9 !important;
  }
  .small-box .icon {
    font-size: 30px !important;
    right: 6px !important;
    top: 6px !important;
    opacity: 0.22 !important;
    z-index: 1 !important;
    pointer-events: none !important;
  }
  ```

---

## 5. Tables & Momentum Scrolling
- Tables with multiple data columns (e.g. Clients, Vouchers, Rates) must be wrapped inside `.table-responsive` with momentum touch scrolling:
  ```css
  .table-responsive {
    -webkit-overflow-scrolling: touch !important;
    scrollbar-width: thin !important;
    overscroll-behavior-x: contain !important;
    border-radius: 6px !important;
  }
  .table th, .table td {
    padding: 0.5rem 0.45rem !important;
    font-size: 0.78rem !important;
    white-space: nowrap !important;
    vertical-align: middle !important;
  }
  ```
- **Actions Column Width:** Multi-button action groups (e.g. `15m`, `Pause/Resume`, `Edit`, `Kick`) must have a minimum width on their header and cells (`min-width: 240px !important`) to prevent cramped wrapping.

---

## 6. iOS Safe Areas (`viewport-fit=cover`)
- For modern iPhones with Dynamic Island and home indicator gesture bars:
  ```css
  :root {
    --sat: env(safe-area-inset-top, 0px);
    --sab: env(safe-area-inset-bottom, 0px);
    --sal: env(safe-area-inset-left, 0px);
    --sar: env(safe-area-inset-right, 0px);
  }
  body {
    padding-bottom: var(--sab) !important;
  }
  .main-header {
    padding-top: var(--sat) !important;
    padding-left: calc(0.5rem + var(--sal)) !important;
    padding-right: calc(0.5rem + var(--sar)) !important;
  }
  .content-wrapper {
    padding-left: calc(8px + var(--sal)) !important;
    padding-right: calc(8px + var(--sar)) !important;
    padding-bottom: calc(14px + var(--sab)) !important;
  }
  .main-sidebar {
    padding-top: var(--sat) !important;
    padding-bottom: var(--sab) !important;
  }
  ```

---

## 7. SweetAlert2 & Action Dialogs
- Ensure modal dialogs fit within the viewport on small mobile devices without clipping buttons:
  ```css
  .swal2-popup {
    width: 92vw !important;
    max-width: 420px !important;
    padding: 1.2rem 1rem !important;
    font-size: 0.88rem !important;
    border-radius: 12px !important;
  }
  .swal2-actions {
    flex-wrap: wrap !important;
    gap: 8px !important;
    width: 100% !important;
  }
  .swal2-actions button {
    width: 100% !important;
    margin: 2px 0 !important;
  }
  ```
