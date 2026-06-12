# Aiszr Design System

> 纯白极简 + 动态交互风格，Apple 风审美。用于 landing page、营销页、展示页。

## 色彩

```
主背景:     #ffffff
主文字:     #1a1a1a
次要文字:   #888888
强调色:     #0066ff (蓝)
成功:       #22c55e
错误:       #ef4444
警告:       #f59e0b
卡片背景:   #f5f5f7
卡片边框:   #e8e8ed
深色背景:   #0a0a0a (用于 stats/hero loader)
```

渐变用于标题高亮: `linear-gradient(135deg, #0066ff, #7c3aed)` — 蓝到紫

## 字体

| 用途 | 字体 | 字重 | 备注 |
|------|------|------|------|
| 标题/大字 | **Syne** | 800 | 几何感强，现代。letter-spacing: -.04em |
| 正文 | **DM Sans** | 300-700 | 清晰中性 |
| 代码/标签 | **JetBrains Mono** | 400-600 | 等宽，用于 logo、标签、terminal |

不要用: Inter, Roboto, Arial, Space Grotesk, Instrument Serif

字号用 `clamp()` 做响应式:
- 大标题: `clamp(2.5rem, 6vw, 4.8rem)`
- 中标题: `clamp(2.2rem, 4.5vw, 3.8rem)`
- 正文: `1.05rem - 1.2rem`
- 小标签: `0.72rem - 0.82rem`

## 间距与布局

```
section padding:   130px 24px (上下130, 左右24)
内容最大宽度:      1100px (居中)
卡片间距:          14-16px
圆角:              28px (大卡片), 15-16px (小元素), 100px (按钮/tag)
```

## 按钮

```css
/* 主按钮 */
.btn-primary {
    background: #0a0a0a;
    color: #fff;
    padding: 17px 38px;
    border-radius: 100px;
    font-weight: 600;
    font-size: 0.95rem;
    hover: translateY(-2px) + box-shadow 0 10px 36px rgba(0,0,0,.25)
}

/* 幽灵按钮 */
.btn-ghost {
    background: rgba(255,255,255,.5);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(0,0,0,.08);
    hover: border-color 变深 + translateY(-2px)
}
```

按钮 hover 时带箭头右移: `translateX(5px)`

## 卡片

```css
背景:     #f5f5f7 (浅灰，不用纯白)
圆角:     28px
边框:     1px solid rgba(0,0,0,.04) (极淡)
hover:    border-color rgba(0,0,0,.06) + box-shadow 0 8px 32px
transition: all .6s cubic-bezier(.23,1,.32,1)
```

不要在卡片上做鼠标跟随光效 (mouse-follow glow)，显得廉价。

## 鼠标光标

全局隐藏默认光标，使用自定义光标:

```
小圆点:    6px, 黑色, mix-blend-mode: difference
光圈:      36px, 边框 rgba(0,102,255,.2), 蓝色半透明
hover:     光圈膨胀到 60px + 淡蓝填充 rgba(0,102,255,.04)
click:     圆点放大到 10px 变蓝, 光圈缩小到 28px
手机端:    隐藏自定义光标, 恢复默认
```

光圈跟随使用 rAF + lerp (0.12 系数), 不用 GSAP。

## 背景纹理

```css
/* 点阵背景 — 极淡 */
body::before {
    background-image: radial-gradient(circle, rgba(0,0,0,.04) 1px, transparent 1px);
    background-size: 32px 32px;
    opacity: 0.6;
}
```

不要用: 线条网格、噪点 SVG、渐变 mesh (太重)

## 动画原则

### 入场动画 — 每个 section 必须不同

| Section | 动画方式 |
|---------|---------|
| Hero 标题 | 字符逐个 fadeIn + translateY(40px) |
| Bento 卡片 | 从 6 个方向飞入 + 随机顺序 (rotation + x + y) |
| Stats | 从中心 scale(.7) 弹出 back.out |
| Architecture | 从左到右级联 x:-40 |
| Showcase 横滑 | scrub:5 慢速水平移动 |
| Ecosystem | IntersectionObserver + 简单淡入 y:20→0 |
| CTA | scale(.85) + blur(8px) 渐显 |

### 关键规则

- **不要用 `gsap.from` + ScrollTrigger** — 不可靠，元素会提前变透明。用 `gsap.set` + `ScrollTrigger.create(onEnter)` 或 **IntersectionObserver**
- 横向滚动用 `scrub:5` (慢) 不要用 `scrub:1` (太快看不清)
- stagger 用 `0.05 - 0.12` 秒
- easing: 入场用 `power3.out` / `back.out(1.5-2)`，弹性回弹用 `elastic.out(1,.4)`

### hover 动画

```
卡片:    translateY(-6px 到 -8px) + shadow 增强
图标:    scale(1.12) rotate(-4deg) translateY(-2px)
按钮:    translateY(-2px) + shadow
生态item: translateY(-7px)
```

所有 transition 用 `cubic-bezier(.23,1,.32,1)` — 这是 iOS 弹性曲线

## Section 样式模板

### 标题区
```
eyebrow:  0.72rem, 大写, letter-spacing:3px, 蓝色, JetBrains Mono
title:    Syne 800, clamp(2.2rem,4.5vw,3.8rem), -0.04em
desc:     DM Sans, 1.05rem, #888, max-width:480px, line-height:1.75
margin:   eyebrow→title 14px, title→desc 14px, desc→content 60px
```

### Stats (深色区)
```
背景: #0a0a0a
数字: DM Sans 700, clamp(2.8rem,5vw,4.2rem), 渐变白色
标签: 0.82rem, rgba(255,255,255,.35)
```

### Footer
```
padding: 48px
border-top: 1px solid #e8e8ed
flex, space-between
```

## 导航栏

```
高度: 64px
滚动后: backdrop-filter: blur(24px) + saturate(180%), 半透明白背景
下划线 hover: 底部 1.5px 蓝色线, width 0→100%, cubic-bezier(.23,1,.32,1)
```

## 避免清单

- 不用渐变色做卡片背景 (除了 showcase blue 卡)
- 不用 `font-style:italic` 在大标题上 (文字变宽容易溢出)
- 不用 `-webkit-text-fill-color:transparent` + 渐变 (渲染不稳定，用纯色 `color:var(--accent)` 替代)
- 不用 SVG 画架构连线 (用 CSS `::before` + `::after` 伪元素)
- 不要在全局 document 上监听 click 做 ripple (只在 `.btn` 上)
- 不用紫色渐变做全局背景 (太 AI slop)
- 统计数字不要用 Syne 800 (太扁)，用 DM Sans 700
