# 客户端视觉规范

2026-10-12 按用户要求统一更新桌面界面。设计参考网上的
[pyside6-fluent-ui 技能](https://github.com/ejacques11/pyside6-fluent-ui)
和 [PyQt-Fluent-Widgets 组件图库](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)。
项目继续使用现有 PySide6 控件；本次未引入新的组件库依赖。

- 深蓝导航栏承担模块切换；内容区域使用中性背景、白色卡片和蓝色主操作。
- 中文使用 Microsoft YaHei UI，英文使用 Segoe UI，已有 Noto Sans SC 作为后备字体。
- 正文 13 px、辅助文字 12 px、页面标题 26 px；普通控件 7 px 圆角、卡片 12 px 圆角。
- 字段、下拉箭头、复选框、菜单、表格、标签页和弹窗统一定义交互状态。
- `ui/design.py` 提供浅色 / 深色语义颜色、QPalette 和 QSS；绘制的任务、项目行复用相同颜色。
- `ui/theme.py` 保留既有页面的布局规则，通过兼容映射接入语义颜色，避免视觉调整影响业务。
- 本次保持系统标题栏；不添加大型滚动容器阴影或常驻动画。

`docs/ui-preview-fluent/` 中的截图使用隔离样例数据，供视觉对照，不代表真实任务与项目。
业务数据库和已有工作区未用于截图。
