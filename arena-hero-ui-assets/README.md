# Arena Hero Agent UI 素材包

这套素材对应 Arena Hero Agent 深色战术控制台设计稿，全部为原创 SVG，可无损缩放，并通过 `currentColor` 在友军蓝、敌军红、Beacon 金等状态间切换。

## 目录

```text
arena-hero-ui-assets/
├── brand/
│   ├── logo-mark.svg
│   └── logo-lockup.svg
├── entities/
│   ├── core.svg
│   ├── worker.svg
│   ├── vanguard.svg
│   └── ranger.svg
├── map/
│   ├── beacon.svg
│   ├── resource-crystal.svg
│   ├── obstacle.svg
│   ├── risk-cell.svg
│   └── grid-tile.svg
├── icons/
│   └── ui-symbols.svg
├── png/
│   ├── logo-mark-128.png
│   ├── logo-lockup-720.png
│   └── core/worker/vanguard/ranger/beacon/resource/obstacle 透明 PNG
├── reference/
│   └── dashboard-ui-concept.png
├── styles/
│   └── tokens.css
├── ASSET-MANIFEST.json
├── contact-sheet.png
├── preview.html
└── LICENSE-ASSETS.md
```

## 推荐尺寸

| 素材 | 地图尺寸 | 卡片/表格尺寸 |
| --- | ---: | ---: |
| Core | 40–48 px | 24 px |
| Worker | 26–32 px | 18–20 px |
| Vanguard | 28–34 px | 18–20 px |
| Ranger | 28–34 px | 18–20 px |
| Beacon | 34–42 px | 20–24 px |
| Resource | 20–28 px | 18–20 px |
| 状态/操作图标 | — | 16–20 px |

所有图标的安全缩放范围为 16–128 px。建议对地图实体添加 1–2 px 深色外描边或轻微投影，以免在障碍和风险热区上丢失轮廓。

`png/` 是便于原型工具或 Canvas 贴图直接使用的 128 px 透明导出；正式 Web UI 优先使用 SVG。`contact-sheet.png` 是核心素材总览。
`reference/dashboard-ui-concept.png` 是这些素材对应的完整控制台视觉参考。

## 颜色用法

```html
<img src="entities/worker.svg" class="entity entity--friendly" alt="Worker">
```

普通 `<img>` 不能从父级继承 `currentColor`。需要动态换色时使用内联 SVG 或符号精灵：

```html
<svg class="entity entity--friendly" aria-label="Worker">
  <use href="icons/ui-symbols.svg#entity-worker"></use>
</svg>

<svg class="entity entity--enemy" aria-label="Enemy Ranger">
  <use href="icons/ui-symbols.svg#entity-ranger"></use>
</svg>
```

```css
.entity { width: 32px; height: 32px; }
.entity--friendly { color: var(--ah-friendly); }
.entity--enemy { color: var(--ah-danger); }
.entity--beacon { color: var(--ah-beacon); }
```

如果以 `<img>` 引入，独立实体 SVG 的默认色是友军蓝；敌军版本可以通过 CSS mask 或构建时替换颜色生成。

## UI 符号精灵

`icons/ui-symbols.svg` 可用的 symbol ID：

```text
brand-mark
entity-core entity-worker entity-vanguard entity-ranger
map-beacon map-resource map-obstacle map-waypoint
status-live status-success status-warning status-danger status-info
metric-resource metric-population metric-health metric-shield
control-play control-pause control-stop control-zoom-in control-zoom-out
control-crosshair control-filter control-chevron-down
action-move action-attack action-harvest action-deposit action-heal
```

使用方式：

```html
<svg class="icon" aria-hidden="true">
  <use href="icons/ui-symbols.svg#control-pause"></use>
</svg>
```

通过本地 HTTP 服务预览，避免浏览器对 `file://` 外部 SVG `<use>` 的安全限制：

```bash
cd arena-hero-ui-assets
python -m http.server 8080
```

打开 `http://127.0.0.1:8080/preview.html`。

## 地图渲染建议

- 网格、雾区、风险热区和移动路线应使用 Canvas 动态绘制；这里的 SVG 用于设计对照和静态回退。
- `grid-tile.svg` 为 32 px 基础格，可作为 CSS background。
- `risk-cell.svg` 使用红色半透明斜纹，不应单独代表风险等级；同时显示数字或文本。
- 迷雾不是一个固定图片素材，应由当前可见掩码实时计算。
- 当前敌人进入迷雾后必须从实体图层删除，不能变成“半透明敌人”。
- 历史资源线索可降低不透明度并增加虚线外圈，但当前视野已否定的资源应立即移除。

## 字体

推荐：

```css
font-family: Inter, "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif;
```

本素材包不内置第三方字体文件。数字、Tick 和坐标可以使用 `JetBrains Mono` 或系统等宽字体。

## 无障碍

- 图标颜色不是唯一状态信号；同时使用标签、形状和 `aria-label`。
- 正文与面板背景对比度至少 4.5:1。
- 交互控件点击区域至少 40×40 px。
- 地图实体需要可键盘聚焦，并在侧栏提供同等信息。
