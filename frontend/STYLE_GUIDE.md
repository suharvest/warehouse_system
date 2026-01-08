# UI Style Guide

基于 Tailwind CSS 的简洁设计系统，可直接复制到其他项目。

---

## 快速开始

```bash
# 依赖
npm install tailwindcss postcss autoprefixer postcss-import
```

```js
// tailwind.config.js
import preset from './design-system/tailwind.preset.js'
export default { presets: [preset], content: ['./src/**/*.{html,js}'] }
```

---

## 设计变量

### 颜色

```css
:root {
  /* 品牌色 */
  --primary: #8cc63f;
  --primary-hover: #7ab32f;
  --primary-light: #e8f5d6;

  /* 语义色 */
  --success: #52c41a;
  --warning: #faad14;
  --danger: #ff4d4f;
  --info: #1890ff;

  /* 背景 */
  --bg-body: #f4f7f0;
  --bg-card: #ffffff;
  --bg-header: #e9f0e1;
  --bg-hover: #f5f7fa;

  /* 文字 */
  --text-primary: #1f1f1f;
  --text-secondary: #666666;
  --text-muted: #999999;

  /* 边框 */
  --border: #e8e8e8;
  --border-light: #f0f0f0;
}
```

### 尺寸

| 用途 | 值 |
|------|-----|
| 圆角-小 | 4px |
| 圆角-默认 | 6px |
| 圆角-大 | 12px |
| 侧边栏宽度 | 240px |
| 头部高度 | 64px |
| 阴影-卡片 | `0 2px 8px rgba(0,0,0,0.05)` |
| 过渡 | 200ms |

### 字体

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;

/* 字号 */
--text-xs: 12px;
--text-sm: 14px;
--text-base: 16px;
--text-lg: 18px;
--text-xl: 20px;
```

---

## 核心组件 CSS

### 布局

```css
/* 应用容器 */
.app-wrapper { display: flex; height: 100vh; }
.sidebar { width: 240px; background: #fff; border-right: 1px solid #e8e8e8; }
.main-content { flex: 1; display: flex; flex-direction: column; background: #f4f7f0; }
.content-area { flex: 1; overflow-y: auto; padding: 24px; }
```

### 导航

```css
.nav-item {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 16px; border-radius: 8px;
  color: #666; font-size: 14px; cursor: pointer;
  transition: all 200ms;
}
.nav-item:hover { background: #f5f7fa; color: #1f1f1f; }
.nav-item.active { background: #e8f5d6; color: #8cc63f; font-weight: 600; }
```

### 卡片

```css
.card {
  background: #fff;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.card:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
  transform: translateY(-2px);
}
```

### 按钮

```css
.btn {
  padding: 8px 16px; border-radius: 8px;
  font-size: 14px; font-weight: 500;
  cursor: pointer; transition: all 200ms;
  display: inline-flex; align-items: center; gap: 8px;
}
.btn-primary { background: #8cc63f; color: #fff; border: none; }
.btn-primary:hover { background: #7ab32f; }
.btn-secondary { background: #fff; color: #1f1f1f; border: 1px solid #e8e8e8; }
.btn-secondary:hover { border-color: #8cc63f; color: #8cc63f; }
.btn-danger { background: #fff1f0; color: #ff4d4f; border: 1px solid #ffccc7; }
.btn-danger:hover { background: #ff4d4f; color: #fff; }
```

### 表单

```css
.form-group { margin-bottom: 16px; }
.form-group label {
  display: block; margin-bottom: 6px;
  font-size: 13px; color: #666; font-weight: 500;
}
.form-input {
  width: 100%; padding: 8px 12px;
  border: 1px solid #e8e8e8; border-radius: 6px;
  font-size: 14px; transition: all 200ms;
}
.form-input:focus {
  outline: none; border-color: #8cc63f;
  box-shadow: 0 0 0 2px rgba(140,198,63,0.2);
}
```

### 表格

```css
table { width: 100%; border-collapse: collapse; }
th {
  background: #e9f0e1; color: #666;
  padding: 16px 24px; text-align: left;
  font-weight: 500; border-bottom: 1px solid #e8e8e8;
}
td {
  padding: 16px 24px; border-bottom: 1px solid #f0f0f0;
  font-size: 14px;
}
tbody tr:hover { background: #fafafa; }
tbody tr.clickable { cursor: pointer; }
```

### 徽章

```css
.badge {
  padding: 4px 8px; border-radius: 4px;
  font-size: 12px; font-weight: 500;
}
.badge-success { background: #effce8; color: #52c41a; }
.badge-warning { background: #fff7e6; color: #faad14; }
.badge-danger { background: #fff1f0; color: #ff4d4f; }
.badge-info { background: #e6f7ff; color: #1890ff; }
.badge-muted { background: #f5f5f5; color: #999; }
```

### 模态框

```css
.modal {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.5); z-index: 1000;
  align-items: center; justify-content: center;
}
.modal.show { display: flex; }
.modal-content {
  background: #fff; border-radius: 12px;
  width: 90%; max-width: 500px; max-height: 90vh;
  overflow-y: auto;
}
.modal-header {
  padding: 20px 24px; border-bottom: 1px solid #e8e8e8;
  display: flex; justify-content: space-between; align-items: center;
}
.modal-body { padding: 24px; }
.modal-footer {
  padding: 20px 24px; border-top: 1px solid #e8e8e8;
  display: flex; justify-content: flex-end; gap: 12px;
}
```

### 筛选栏

```css
.filter-bar {
  background: #fff; border-radius: 12px;
  padding: 20px; margin-bottom: 20px;
  display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-end;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.filter-group { display: flex; flex-direction: column; gap: 8px; }
.filter-group input, .filter-group select {
  min-width: 180px; padding: 8px 12px;
  border: 1px solid #e8e8e8; border-radius: 6px;
}
.filter-actions { margin-left: auto; display: flex; gap: 12px; }
```

### 分页

```css
.pagination {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 24px; border-top: 1px solid #e8e8e8;
}
.pagination-controls { display: flex; align-items: center; gap: 8px; }
.pagination-controls button {
  padding: 6px 12px; border: 1px solid #e8e8e8;
  background: #fff; border-radius: 4px; cursor: pointer;
}
.pagination-controls button:disabled { opacity: 0.5; cursor: not-allowed; }
```

---

## 交互模式

### 悬停效果
- 卡片: 上移 2px + 加深阴影
- 按钮: 背景色变深或边框变色
- 表格行: 浅灰背景 `#fafafa`

### 焦点状态
- 输入框: 绿色边框 + 绿色外发光 `box-shadow: 0 0 0 2px rgba(140,198,63,0.2)`

### 动画
```css
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in { animation: fadeIn 0.3s ease; }
```

### Tab 切换
- 使用 `.active` 类控制显示
- 切换时触发 `fadeIn` 动画

---

## 响应式断点

```css
@media (max-width: 768px) {
  .app-wrapper { flex-direction: column; }
  .sidebar { width: 100%; border-right: none; border-bottom: 1px solid #e8e8e8; }
  .side-nav { flex-direction: row; overflow-x: auto; }
  .stats-grid, .dashboard-grid { grid-template-columns: 1fr; }
}
```

---

## 迁移清单

1. 复制 `design-system/` 目录
2. 安装 Tailwind + PostCSS
3. 配置 `tailwind.config.js` 引入 preset
4. 在 CSS 入口引入:
   ```css
   @import './design-system/base.css';
   @import './design-system/components.css';
   @tailwind base;
   @tailwind components;
   @tailwind utilities;
   ```
5. 可选: 下载 Inter 字体到 `assets/fonts/`
