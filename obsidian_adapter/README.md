# Obsidian 独立测试库适配

这是导出适配试验，不是已完成的Obsidian自动同步插件。服务器和Flutter客户端均保留。

## 使用测试库

完整解压测试库ZIP。在Windows Obsidian选择「打开文件夹作为仓库」，指向解压后包含「笔记」「附件」的目录；不要只打开ZIP。Android先解压到可访问本地目录，再在Obsidian中打开该文件夹。Vault不包含第三方插件、主题或执行脚本；使用核心阅读/属性/搜索即可。

每篇文件名是笔记/pkm-ID.md，标题变化不会改文件名。Frontmatter包含来源、标签、完整度等；AI摘要优先，原文和附件位于默认折叠callout。真实排版和移动端长文体验仍需Obsidian实机验证。

## 重复导出

Python标准库，无需pip。项目根运行：

```powershell
python -m obsidian_adapter.export --directory "D:\PKM备份\Obsidian测试库" --allow-http --zip "PKM-Obsidian-next.zip"
```

密码交互输入且不保存，当前HTTP会明文传输管理密码/会话，不建议长期使用；默认地址只是临时IP。正式HTTPS后移除allow-http并用--server指定。未来连接器应使用设备只读配对而非管理密码。

目标必须为专用空目录，或已经有适配器marker的目录。不会向现有非空Obsidian库直接写入。远端缺失笔记保留；本地生成文件修改后不静默覆盖，更新内容进入「冲突」目录。请将个人批注放独立笔记，通过链接关联，避免与服务器生成区混写。

## 测试和边界

```powershell
python -m unittest discover -s obsidian_adapter -t . -v
```

导出只获取数据，不生成AI、不改变服务器笔记。图片仅使用服务器已保存的哈希附件并校验；正文远程图片/嵌入语法安全降级，不自动下载；原始HTML按文本转义。ZIP使用白名单，不含密码、token、SDK、其他库或任意Obsidian插件。

本轮不包含设备配对插件、自动同步、手机后台、双向编辑或多设备冲突合并。ZIP分发不是持续同步；下一阶段需实现兼容Windows/Android的Obsidian连接器并验证本地修改保护。
