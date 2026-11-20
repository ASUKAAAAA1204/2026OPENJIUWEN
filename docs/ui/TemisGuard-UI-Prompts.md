# TemisGuard 天序明衡 · UI 参考图提示词 v2

> 配套文档：`TemisGuard-PRD-v2.7.pdf`
> 视觉母本：明日方舟「众生行记」（赛博宗教 / 神圣极简主义 / 蓝图美学 / 古典解构）
> 生成模型：本地 ComfyUI · MiniMax H3 Image（w4a8 扩散 + Qwen3VL-32B nvfp4 文本编码器）

**v2 变更**：通用风格、背景参数、排除项已全部并入每一格的正文，**每格只有一段文本，复制即提交**，不再有需要单独拼接的「风格块 / 背景块 / 排除块」。改风格时需 14 处同步替换。

---

## 0. 执行规范

| 项目 | 取值 |
|---|---|
| sampler / scheduler | `euler` / `simple` |
| steps | 8 |
| cfg | 1.0 |
| 基线 seed | `20261118`（同批次保持一致；构图微调可 ±1） |
| enhance_prompt | **关闭**（会改写风格词，破坏一致性） |
| negative prompt | **不可用**（该模型无负向条件，排除项已写入正向末尾） |
| 输出目录 | `D:\resource\3d\ComfyUI\output\temisguard_ui\` |
| 命名 | `ui_{编号}_{slug}.png`，例 `ui_01_dashboard.png` |

**关于文字**：模型无法稳定生成真实字形。所有提示词统一要求「等宽微字仅作纹理、不成词」，实现阶段由前端替换真实文案。不要试图让模型写出 `ALLOW`、`决策流水` 这类可读字样。

---

## 1. 设计令牌

提示词中的每个数值都出自此表，前端实现一一对应，不允许自由发挥。

**色值（全站仅此 7 个）**

| 令牌 | 色值 | 唯一用途 | 面积预算 |
|---|---|---|---|
| `--bg-base` | `#F5F7FA` → `#FFFFFF` | 页面底色，极缓径向渐变 | 主色域 |
| `--line` | `rgba(0, 47, 167, 0.06)` | 网格、辅助圆、刻度 | ≤ 8% |
| `--ink` | `#1A1D21` | 正文与标题 | 按需 |
| `--ink-mute` | `#9AA3AD` | 微标签、次要信息、水印 | 按需 |
| `--klein` | `#002FA7` | **仅**径向光晕，不做实色 | ≤ 1% |
| `--gold` | `#EAD2A8` | 圣光金，**仅**细线与小图形描边 | ≤ 2% |
| `--crimson` | `#C0443A` | 余晖红，**仅** DENY / 不可复现 | ≤ 1% |

> 硬规则：除金、红两色外，所有彩色像素必须可归入 `--klein` 光晕。出现任何第三色实色块，该图判不合格。

**线 / 字 / 距**

| 项目 | 取值 |
|---|---|
| 主描边 | 1px，全图唯一线宽；虚线 `1px dash 4/4` |
| 次级描边 | 0.5px（仅辅助圆、内层虚线环） |
| 圆角 | 一律 **0**；胶囊按钮 `999px` |
| 标题字 | 高对比衬线，字距 `0.24em`，全大写占位 |
| 正文字 | 无衬线 12–14px，行高 1.7 |
| 微标签 | 等宽 10px，字距 `0.12em`，色 `--ink-mute` |
| 间距基数 | 8 的倍数：`8 / 16 / 24 / 32 / 48 / 64` |
| 玻璃层 | 白色 62% 不透明 + 1px 边，不叠加模糊光斑 |
| 遮罩 | 弹层整体压暗 **4%**（灰化，不变黑） |
| 水印字 | 不透明度 **3%**，只作纹理，不得可读 |

---

## 2. 背景规范（参数对照，不单独提交）

五类背景互相不可替换，每格正文中已按本表写死对应参数。

| 代号 | 名称 | 参数 |
|---|---|---|
| **BG-A** | 高调白网格 | `#F5F7FA`→`#FFFFFF` 极缓径向渐变（中心略亮）；24px 间距 1px 钴蓝网格 6% 不透明度；四边内侧 32px 处 2px 刻度短线，每 5 格加长至 4px |
| **BG-B** | 圣光雾霭 | 全白雾面；右上 35° 斜射丁达尔光束，宽占画面 40%，末端散成细粒；40–60 颗 1–2px 白色微尘，密度向下递减 |
| **BG-C** | 蓝图辅助圆 | 白底叠三层同心圆，半径比 1 : 1.618 : 2.618，线宽 1px、不透明度 4%，圆心在水平 38.2% / 垂直 61.8%，带 0.5° 刻度 |
| **BG-D** | 竖排水印字 | 右侧 12% 宽度竖排超大衬线单词（`TEMISGUARD`），字号占画面高 70%，不透明度 3%，仅作纹理 |
| **BG-E** | 纯净白 | 纯 `#FFFFFF`，无渐变无网格，仅四角 8px L 形对位标记 |

**禁止背景**：深色/夜景、渐变紫、重噪点、纸张纹理、真实照片景深、过强玻璃折射。

---

## 3. 分格限定总表

**彩色预算**＝金/红细线可占面积上限；**留白下限**＝无任何线条与图元覆盖的白色面积。任一项越界即重跑，不修补。

| # | 文件 | 尺寸 | 背景 | 彩色预算 | 留白下限 | 专属禁止项 |
|---|---|---|---|---|---|---|
| 01 | `ui_01_dashboard` | 1216×768 | BG-B | 2% | 55% | 可读数字、三栏以上布局、卡片投影 |
| 02 | `ui_02_decision_stream` | 1024×1024 | BG-D | 1.5% | 50% | 实心卡片、彩色时间条、箭头图标 |
| 03 | `ui_03_evidence_drawer` | 1024×1024 | BG-A | 1% | 45% | 写实屏幕截屏感、渐变遮罩、滚动条 |
| 04 | `ui_04_gate_states` | 1024×1024 | BG-E | 2% | 70% | 填充徽章、徽章不等大、彩色底块 |
| 05 | `ui_05_ask_approval` | 1216×768 | BG-B | 2% | 50% | 暗色弹窗、实心主按钮、红色拒绝按钮 |
| 06 | `ui_06_uncertainty_debt` | 1024×1024 | BG-C | 1.5% | 55% | 饼图配色、扇区填充、3D 圆环 |
| 07 | `ui_07_readings` | 1216×768 | BG-C | 1% | 60% | 霓虹仪表盘、彩色指针、数字实心化 |
| 08 | `ui_08_run_tree` | 1024×768 | BG-A | 0.5% | 60% | 深色树、节点填充、曲线连接线 |
| 09 | `ui_09_replay_diff` | 1216×768 | BG-D | 1% | 50% | 红绿 diff 高亮块、两列不等宽、并排表头 |
| 10 | `ui_10_release_gate` | 1216×768 | BG-B | 2% | 50% | 写实开关、闸门立体化、柱状图 |
| 11 | `ui_11_remediation_quote` | 1024×1024 | BG-E | 1% | 60% | 纸质发票纹理、表格斑马纹、实心印章 |
| 12 | `ui_12_mobile_approval` | 640×1024 | BG-B | 2% | 45% | 手机外壳刘海、手势条、状态栏图标 |
| 13 | `ui_13_empty_loading` | 1024×1024 | BG-A | 0.5% | 65% | 旋转 spinner、灰块骨架屏、渐变进度条 |
| 14 | `ui_14_icon_family` | 1024×1024 | BG-E | 0% | 75% | 图标填充、圆角端点外扩、间距不均 |

---

## 4. 分格提示词

以下每格**只有一段文本**，从 `High-key` 读到 `no watermark` 全文复制提交，不接受增删拼接。

### 01 工作台总览 — 1216×768 · BG-B · 运行状态 / Gate P95 / 债务 / 覆盖率

```
High-key editorial security console dashboard hero in Arknights "Exusiai the New Covenant" art
direction, sacred minimalist blueprint aesthetic: one deconstructed halo totem exactly centered at
46% of frame height, built from a 1px outer ring, a 0.5px dashed middle ring, a twelve-sided polygon
and a hairline cross, with twelve evenly spaced tick nodes on the outer ring; directly beneath it
eight semicircular gauge dials in a 2x4 grid on 1px tick scales, only the top-left dial edged in
antique gold #EAD2A8; four corner clusters of 10px monospaced micro labels tethered to the totem by
45 and 90 degree hairline leaders, representing run status, gate p95 latency, uncertainty debt and
coverage; background is white fog washed from #F5F7FA to #FFFFFF with one 35 degree god ray entering
from the upper right, 40 to 60 floating dust motes thinning downward, a faint #E9EEF7 radial glow
behind the totem and a 24px cobalt blueprint grid at 6% opacity with 2px edge ticks; panels are
frosted white at 62% opacity bounded only by 1px edges and L-shaped registration corners; palette
limited to #1A1D21 ink, #9AA3AD muted ink and 1px gold or crimson strokes, perfectly symmetric
composition, ultra clean precision, no legible words, no readable numerals, no thick borders, no
drop shadows, no 3D bevels, no neon, no saturated fills, no purple gradients, no cartoon, no logo,
no watermark
```

### 02 决策流水时间轴 — 1024×1024 · BG-D · 时间序 / 四态 / 耗时

```
Vertical decision stream timeline in Arknights "Exusiai the New Covenant" art direction, sacred
minimalist blueprint aesthetic: a single 1px vertical axis line down the exact frame center, one
hollow 6px circular hash node every 96 pixels, translucent frosted glass cards alternating left and
right anchored to each node, each card bounded only by a 1px top edge and a 1px left edge with an
L-shaped registration corner at its top right, every card holding three rows, a row of 10px
monospaced micro labels, a 3px hollow state dot beside two-digit numerals, and an 8px minimal
duration bar; state is carried only by hairline ring color, antique gold #EAD2A8 for allow, muted
crimson #C0443A for deny, slate and pale grey for the rest; background is #F5F7FA to #FFFFFF with a
24px cobalt blueprint grid at 6% opacity, an oversized vertical serif word "TEMISGUARD" at 3%
opacity on the right edge as pure texture, faint floating dust motes and a barely visible #002FA7
glow; palette limited to #1A1D21 ink and #9AA3AD muted ink beyond those strokes, desaturated
disciplined rhythm, no readable sentences, no filled cards, no thick borders, no drop shadows, no
3D bevels, no neon, no saturated fills, no arrow icons, no purple gradients, no cartoon, no
watermark
```

### 03 证据审查抽屉 — 1024×1024 · BG-A · 双摘要 / 链线 / 印章

```
Evidence inspection drawer floating above a wireframe console, Arknights "Exusiai the New Covenant"
art direction, sacred minimalist blueprint aesthetic: a tall frosted glass drawer occupying the
right 62% of the frame, its four stacked sections divided by 1px dashed rules, first a key-value
field list in 10px monospaced micro type with hairline row separators, second two side-by-side
digest blocks of equal width, third a single thin chain line running vertically upward and exiting
the drawer's top edge, fourth a square seal glyph built from a double hairline frame, concentric
circles and a tiny cross; the console behind is reduced to 20% opacity outline only, no content
inside it; background is #F5F7FA to #FFFFFF with a 24px cobalt blueprint grid at 6% opacity and
2px edge ticks; the drawer outline is 1px with L-shaped corner marks and no fill beyond 62% white
frost; palette limited to #1A1D21 ink, #9AA3AD muted ink, one #EAD2A8 hairline accent and a faint
#002FA7 edge glow, floating dust motes, desaturated and precise, no legible words, no screenshot
look, no gradient veil, no thick borders, no drop shadows, no 3D bevels, no neon, no saturated
fills, no scrollbars, no purple gradients, no cartoon, no watermark
```

### 04 Gate 四态徽章组 — 1024×1024 · BG-E · ALLOW / ASK / DENY / REWRITE

```
Four gate state badges in a strict 2x2 grid, each exactly 320px square with 64px gutters, on pure
white #FFFFFF, Arknights "Exusiai the New Covenant" art direction, sacred minimalist blueprint
aesthetic: every badge is an unfilled 1px frame with L-shaped corner registration marks and a
hairline blueprint grid confined inside it, no fill anywhere; the first badge contains a closed
circle with one upward arrow drawn in antique gold #EAD2A8, the second a circle broken by a gap
with two small opposing arrows in gold and pale grey, the third a circle severed by one diagonal
line in muted crimson #C0443A, the fourth two offset arc segments in slate grey replacing the
circle entirely; one line of 10px monospaced placeholder micro text sits beneath each badge; only
four corners of the sheet carry 8px L marks, the rest of the canvas is empty white; palette limited
to #1A1D21 ink, #9AA3AD muted ink and those two accent colors as 1px strokes, perfect geometric
precision, no filled badges, no unequal badge sizes, no colored background blocks, no legible
words, no thick borders, no drop shadows, no 3D bevels, no neon, no saturated fills, no purple
gradients, no cartoon, no watermark
```

### 05 ASK 人工审批弹层 — 1216×768 · BG-B · 30 秒倒计时 / 批准 / 拒绝

```
Modal approval card centered in a high-key white fog scene, Arknights "Exusiai the New Covenant"
art direction, sacred minimalist blueprint aesthetic: a 420x600 frosted glass card at 62% white
with only a 1px outline and L-shaped corners, at its top a 96px circular countdown ring drawn with
one 1px arc and twelve tick nodes around an empty center, inside the card three sections divided by
1px dashed rules, a two-line action digest above a flat 8px summary bar, a three-line reasoning
block in ultra thin type, and two pill buttons at the bottom where the left one is outlined in
antique gold #EAD2A8 hairline with a soft inner glow and the right one is text only; background is
white fog dimmed by a 4 percent grey veil, never darkened, with one 35 degree god ray from the
upper right, 40 floating dust motes and a faint #E9EEF7 halo behind the card; palette limited to
#1A1D21 ink and #9AA3AD muted ink beyond the gold outline, generous negative space, no dark modal,
no filled primary button, no red reject button, no legible words, no thick borders, no drop
shadows, no 3D bevels, no neon, no saturated fills, no purple gradients, no cartoon, no watermark
```

### 06 不确定性标记与债务仪表 — 1024×1024 · BG-C · 三类标记 / 加权债务

```
Uncertainty marks and debt instrument panel, Arknights "Exusiai the New Covenant" art direction,
sacred minimalist blueprint aesthetic: three concentric guide circles at 4% opacity centered on a
golden ratio point at 38.2% horizontal and 61.8% vertical with radii in the ratio 1 to 1.618 to
2.618, one large 1px circle 72% of frame width divided into three equal sectors by three radii, each
sector edge carrying a small line-art symbol, a ring with a gap, two broken parallel lines, and a
set of offset tick marks; a compact weighted score cluster sits at the exact center with one large
ultra-thin numeral and three lines of 10px monospaced micro labels beneath it; a 45 degree leader
line folds out to three vertically stacked frosted entry cards at the lower left; background is
#F5F7FA to #FFFFFF with a 24px cobalt grid at 6% opacity; a single #EAD2A8 hairline accent marks one
sector edge and a faint #E9EEF7 radial glow sits behind the center; palette limited to #1A1D21 ink
and #9AA3AD muted ink, floating dust motes, precise symmetry, no pie chart colors, no filled
sectors, no 3D rings, no legible words, no thick borders, no drop shadows, no 3D bevels, no neon,
no saturated fills, no purple gradients, no cartoon, no watermark
```

### 07 八项读数刻度盘阵列 — 1216×768 · BG-C · 八指标 / 一例不适用

```
Array of eight half-circle reading gauges in a strict 4x2 grid, each with 110px radius and a 180
degree arc, Arknights "Exusiai the New Covenant" art direction, sacred minimalist blueprint
aesthetic: every gauge drawn with 1px ticks that lengthen every fifth mark, a single 1.5px needle
ending in a 3px dot, a deliberate 12px blank gap above each gauge center reserved for a future
numeral, and two lines of 10px monospaced micro labels beneath; one gauge is intentionally
unresolved, shown as a fully dashed arc with no needle at all; background is #F5F7FA to #FFFFFF
with a 24px cobalt blueprint grid at 6% opacity, three very faint concentric guide circles at 4%
opacity drawn in the Fibonacci ratio, and 2px edge ticks; one active needle carries the only #EAD2A8
hairline accent and a faint #E9EEF7 glow sits behind the grid center; palette limited to #1A1D21 ink
and #9AA3AD muted ink, floating dust motes, disciplined symmetry, generous white space, no neon
dashboards, no colored needles, no filled numerals, no legible words, no thick borders, no drop
shadows, no 3D bevels, no saturated fills, no colorful data viz palette, no purple gradients, no
cartoon, no watermark
```

### 08 运行与委派树 — 1024×768 · BG-A · run → segment → 委派 / 深度 3 拒绝

```
Vertical run and delegation tree occupying the left 40% of the frame, Arknights "Exusiai the New
Covenant" art direction, sacred minimalist blueprint aesthetic: the root node is a 14px hollow ring,
three levels descend below it each indented 26px, all connectors are 1px right-angle lines that go
down first then across, every node is paired with one line of 10px monospaced micro labels and a 4px
hollow state dot, and the deepest node ends with a short 1px dashed stub and a tiny crossed-out
marker; the right 60% of the frame stays almost entirely empty white with only a faint vertical
serif watermark word at 3% opacity as texture; background is #F5F7FA to #FFFFFF with a 24px cobalt
blueprint grid at 6% opacity and 2px edge ticks; palette limited to #1A1D21 ink, #9AA3AD muted ink
and a barely visible #002FA7 glow, no gold or red needed on this sheet; floating dust motes, ultra
clean precision, no dark tree, no filled nodes, no curved connectors, no legible words, no thick
borders, no drop shadows, no 3D bevels, no neon, no saturated fills, no purple gradients, no
cartoon, no watermark
```

### 09 回放对比视图 — 1216×768 · BG-D · 重放 / 一致率 / 不可复现

```
Side by side replay comparison view, Arknights "Exusiai the New Covenant" art direction, sacred
minimalist blueprint aesthetic: two mirrored columns of equal width, each built from a 1px vertical
line with alternating frosted glass cards hanging off it, three thin horizontal leader lines
bridging the two columns and each carrying a small diamond marker at its midpoint, and one card in
the right column faded to 55% opacity ringed by a dashed muted crimson #C0443A outline to mark it
unreproducible; cards are bounded only by 1px edges and L-shaped corners with 10px monospaced micro
labels inside; background is #F5F7FA to #FFFFFF with a 24px cobalt grid at 6% opacity and an
oversized vertical serif watermark word on the right edge at 3% opacity; palette limited to #1A1D21
ink, #9AA3AD muted ink and the single crimson dashed accent, a faint #E9EEF7 glow, floating dust
motes, strict left-right symmetry, no red-green diff highlights, no unequal column widths, no
side-by-side table headers, no legible words, no thick borders, no drop shadows, no 3D bevels, no
neon, no saturated fills, no purple gradients, no cartoon, no watermark
```

### 10 发布闸与版本对比 — 1216×768 · BG-B · 闸门 / 拦截数 / 版本差

```
Release gate and version diff panel, Arknights "Exusiai the New Covenant" art direction, sacred
minimalist blueprint aesthetic: on the left a vertical gate glyph made of two 1px pillars and three
horizontal breakable hairlines, one of them interrupted by a faint antique gold #EAD2A8 glowing gap;
on the right a three-column comparison table with a solid 1px header rule and 1px dashed row rules,
every row ending in a slim delta bar that points up in gold or down in crimson; a minimal toggle in
the lower left built from two thin outlined circles and one micro label, deliberately flat and
schematic; background is #F5F7FA to #FFFFFF white fog with one 35 degree god ray from the upper
right, 40 floating dust motes, a 24px cobalt grid at 6% opacity and a faint #E9EEF7 glow; all
surfaces are frosted white at 62% with 1px edges and L-shaped corners, palette limited to #1A1D21
ink and #9AA3AD muted ink beyond the two accents, ultra clean precision, no realistic switch, no
three-dimensional gate, no bar charts, no legible words, no thick borders, no drop shadows, no 3D
bevels, no neon, no saturated fills, no purple gradients, no cartoon, no watermark
```

### 11 整改报价单 — 1024×1024 · BG-E · 按条报价 / 权重 / 汇总

```
Remediation quotation sheet, one vertical 620x880 card floating on pure white #FFFFFF,
Arknights "Exusiai the New Covenant" art direction, sacred minimalist blueprint aesthetic: card
edges marked only by L-shaped registration corners with a 1px outline and 62% white frost, a single
wide-tracked serif title line at the top underlined by one 1px rule, a four column item table in
the middle with 44px row height and 1px dashed row separators, a narrow vertical totals block along
the right edge carrying one very large ultra-thin numeral, two square seal glyphs overlapping
slightly at the bottom built from double hairline frames and concentric circles, and 10px
monospaced micro labels everywhere else; a faint 24px cobalt grid at 6% opacity sits behind the card
together with a barely visible #002FA7 edge glow and floating dust motes; palette limited to #1A1D21
ink, #9AA3AD muted ink and one #EAD2A8 hairline accent, no paper invoice texture, no zebra striping,
no filled gold seals, no legible words, no thick borders, no drop shadows, no 3D bevels, no neon,
no saturated fills, no purple gradients, no cartoon, no watermark
```

### 12 窄屏审批卡片 — 640×1024 · BG-B · 值班端 ASK / 30 秒 P95

```
Vertical mobile approval composition, 640x1024 portrait, Arknights "Exusiai the New Covenant" art
direction, sacred minimalist blueprint aesthetic: a frosted white 62% card 86% of width and 62% of
height floating slightly above center, inside it from top to bottom one 10px monospaced state line,
two lines of action digest, a 1px divider, three evidence digest paragraphs each prefixed by a 6px
hollow circle, and at the bottom a full width pill button with an antique gold #EAD2A8 hairline
outline and soft inner glow plus one text-only secondary action; a 72px circular countdown ring with
twelve tick nodes and an empty center floats above the card; background is white fog at #F5F7FA to
#FFFFFF with one 35 degree god ray, 30 floating dust motes thinning downward and a faint #E9EEF7
halo behind the card, plus a 24px cobalt grid at 6% opacity; palette limited to #1A1D21 ink and
#9AA3AD muted ink beyond the gold outline, generous negative space, no phone chassis, no notch, no
gesture bar, no status bar icons, no legible words, no thick borders, no drop shadows, no 3D bevels,
no neon, no saturated fills, no purple gradients, no cartoon, no watermark
```

### 13 空状态与扫描加载 — 1024×1024 · BG-A · 等待写入 / 加载中

```
Empty state and loading composition, Arknights "Exusiai the New Covenant" art direction, sacred
minimalist blueprint aesthetic: at the exact center a thin 1px circle with a 40 degree open gap,
inside it three stacked horizontal hairlines of decreasing length, and beneath it two centered lines
of 10px monospaced micro labels; a 120px wide horizontal scan band at 12% white opacity crosses the
whole frame with slightly brighter 1px edges and six ultra thin vertical striations inside it; small
tick clusters sit in all four corners; background is #F5F7FA to #FFFFFF with a 24px cobalt blueprint
grid at 6% opacity, 2px edge ticks and a faint #E9EEF7 radial glow behind the center, plus a light
scatter of floating dust motes; palette limited to #1A1D21 ink and #9AA3AD muted ink with no gold or
red on this sheet, disciplined symmetry, generous white space, no spinning loader, no grey skeleton
blocks, no gradient progress bar, no legible words, no thick borders, no drop shadows, no 3D bevels,
no neon, no saturated fills, no purple gradients, no cartoon, no watermark
```

### 14 图标族 — 1024×1024 · BG-E · 16 枚线条图标规范

```
Icon family sheet, sixteen line icons arranged in a strict 5x4 grid on pure white #FFFFFF,
Arknights "Exusiai the New Covenant" art direction, sacred minimalist blueprint aesthetic: each
icon is centered in a 160px cell within a 96px bounding box, every stroke is a uniform 1px with
abstract reduced geometry and no rounded caps beyond 0.5px, the set covers in order a halo ring,
a three-piece deconstructed wing, two interlocking chain links, a seal frame with a concentric
circle, a gate of two pillars and three lines, a gauge dial with ticks, a hash node circle, a broken
ring, dual-source arrows, a diamond marker, a delegation tree, a replay arrow loop, an outline price
tag, a role emblem, a thin warning triangle and a thin checkmark; gutters and margins are exactly
equal in both directions, only the four sheet corners carry 8px L marks; all icons render in
#1A1D21 ink with sparse #9AA3AD detail, no gold or red, absolutely no fill, no shadow, extreme
geometric precision, no legible words, no thick strokes, no 3D bevels, no neon, no saturated fills,
no uneven spacing, no purple gradients, no cartoon, no watermark
```

---

## 5. 批量执行顺序

1. **探针**：先跑 04、14。线条变粗、背景变灰或出现渐变紫，说明需加压风格词。
2. **主视觉**：01 → 05 → 12（同 BG-B，验证光束方向与雾面一致）。
3. **数据类**：06 → 07 → 09（同 BG-C / BG-D，验证辅助圆与刻度密度）。
4. **管理类**：10 → 11 → 08 → 02 → 03 → 13。

每张独立提交 API，`filename_prefix` 用 `temisguard_ui/ui_{编号}_{slug}`，直接落到目标目录。

## 6. 出图质检

| 检查项 | 合格标准 |
|---|---|
| 线宽 | 目视全图线条等宽，无局部加粗 |
| 背景 | 与 BG 参数一致：网格 24px、光束 35°、辅助圆三层 |
| 颜色 | 彩色像素只在金/红细线，其余全灰阶 |
| 留白 | 不低于总表中的下限 |
| 文字 | 无清晰可读词句；出现乱码字母即判不合格重跑 |
| 卡片 | 无实心描边、无投影，角标为 L/T 形 |
| 一致性 | 与同批次其它图相比，字体粗细、刻度密度、微尘数量接近 |
