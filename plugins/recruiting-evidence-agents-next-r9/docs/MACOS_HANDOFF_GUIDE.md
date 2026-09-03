# Recruiting Evidence Agents macOS 交接说明

## 一、交接内容和边界

本说明适用于同时收到插件发布包和授权 `materials.zip` 的测试者。插件只读取、整理授权材料；不会改写来源文件、推断录用建议或生成 CEO 决定。收到的 `materials.zip` 是不可变输入。

当前发布版本为 `v0.5.0`，包含五个 Skill：`$base-material-pack-agent`、`$material-pack-agent`、`$candidate-pack-agent`、`$follow-up-incremental-pack-agent` 和 `$hrd-ceo-review-agent`。

当来源是飞书多维表格而非已交付的 `materials.zip` 时，使用 `$base-material-pack-agent`。它以当前用户身份只读 Base 的字段、记录和附件，先在本机临时目录创建可重放快照，再生成同一份 `materials.zip` 和受控登记表。该 Base 快照不是交付物，不能上传、转发或放入最终 `output/`。

一次成功的 Base-to-CEO 处理后，工作目录中的 `output/` 包含以下六个正式文件和一个 `operation_log.jsonl` 审计日志：

```text
output/
  materials.zip
  consolidated_candidate_pack.md
  candidate_pack.html
  source_manifest.md
  missing_evidence_list.md
  ceo_summary_message.md
  operation_log.jsonl
```

Base-to-CEO 包的消息正文保存在 `ceo_summary_message.md`，投递时必须与该文件原文一致，不写入来源 ZIP。普通五文件 Candidate Pack 仍由 HRD 在预览时提供已审阅正文。

## 二、发布包结构

解压插件发布包后，保持以下目录结构不变：

```text
recruiting-evidence-agents-v0-5-0/
  INSTALL_AND_USAGE.md
  .agents/plugins/marketplace.json
  plugins/recruiting-evidence-agents/
  SHA256SUMS.txt
```

其中 `marketplace.json` 使该目录成为可独立安装的本地 Marketplace。发布包不包含候选人材料、飞书账号 profile、凭据、token、收件人或投递历史。

发布 ZIP 旁的同名 `.sha256` 文件用于校验 ZIP 本身；解压目录内的 `SHA256SUMS.txt` 用于校验发布包中的文件。

## 三、环境要求

| 场景 | 必需环境 | 不需要的环境 |
| --- | --- | --- |
| 安装插件 | 支持插件的 macOS、已登录的 Codex Desktop 或 Codex CLI、发布包的本地访问权限 | Python、Node.js、`lark-cli`、飞书账号 |
| 整理已交付材料或本地材料目录 | 上述环境、`python3` 3.9 或更高版本、授权 `materials.zip` 或材料目录 | Base 写权限、机器人身份、复制 profile 或 token |
| 从飞书 Base 读取材料 | 上述环境、网络、`python3` 3.9 或更高版本、`lark-cli`、已授权的当前飞书用户 | Base 写权限、机器人身份、复制 profile 或 token |
| 发送到飞书 | 上述环境、网络、`python3` 3.9 或更高版本、`lark-cli`、组织已授权的飞书自建应用 | 从其他电脑复制 profile、token、App Secret、收件人名单或发送者身份 |

Codex 插件可在 ChatGPT Desktop 的 Codex 和 Codex CLI 中使用，不支持 IDE extension。安装或更新插件后，必须新开一个 Codex 对话，才能加载新的 Skill。

## 四、新电脑安装插件

1. 将插件 ZIP 解压到可写目录，例如 `~/Downloads/recruiting-evidence-agents-v0-5-0`。
2. 在 Terminal 中进入该目录，执行：

   ```bash
   codex plugin marketplace add .
   codex plugin add recruiting-evidence-agents@recruiting-evidence-agents-v0-5-0-marketplace
   codex plugin list --marketplace recruiting-evidence-agents-v0-5-0-marketplace --json
   ```

3. 确认最后一条命令输出中有 `installed: true` 和 `enabled: true`。
4. 新开一个 Codex 对话。可调用的 Skills 为 `$base-material-pack-agent`、`$material-pack-agent`、`$candidate-pack-agent` 和 `$follow-up-incremental-pack-agent`。

如果该 Marketplace 已存在，不要重复添加。先执行 `codex plugin marketplace list --json`，再继续安装命令。

## 五、可直接复制给 Codex 的提示词

### 模板 Base：从授权飞书多维表格开始

```text
使用 $base-material-pack-agent 处理 Base URL <BASE_URL>。
只读该 Base；不要创建、更新、删除字段、记录、附件、视图、权限或 workflow。
在 <SNAPSHOT_DIR> 创建本地快照，在 <WORKSPACE_ROOT>/output/ 生成 materials.zip、临时受控登记表和 Base 材料观察。
若存在 `Confirmed Missing` 项，先由 `$follow-up-incremental-pack-agent` 在同一 output 导出受控的临时 `follow_up_resolution.json`；Candidate Pack 不读取独立控制目录或其他文件夹。材料门禁关闭后，先使用 `manage_hrd_review.py queue` 展示 HRD 面试通过候选人的队列；不得由旧 Candidate Pack 直接生成无 HRD 意见的正式文件。不要把快照、Base URL、file token、凭据或临时登记表放入 output。若材料问题未达到已验证的 `Confirmed Missing` 边界，正式文件不会生成，目录仅保留操作日志。
保留重复、错放和无法归属的材料事实，不要把它们改写为流程结果、HRD 建议或 CEO 决定。
```

首次使用前，当前用户需要本机 `lark-cli` 的 `base:block:read`、`base:field:read`、`base:record:read`、`docs:document.media:download` 与 `offline_access` 授权。授权失败时停止并报告，不得改用 bot 或其他用户。

### 模板 A：从授权原始材料文件夹开始

仅当测试者持有原始、授权的材料目录时使用本模板，而不是已经生成好的 `materials.zip`。

```text
使用 $material-pack-agent 处理目录 <WORKSPACE_ROOT>。
只读取该目录；不要读取任何历史 output、参考输出、临时文件或其他目录。
将纯净 materials.zip 写入 <WORKSPACE_ROOT>/output/，并报告文件数和字节级 SHA-256 校验结果。
完成后使用 $candidate-pack-agent 只读取刚生成的 materials.zip，
在同一 <WORKSPACE_ROOT>/output/ 生成其余四个正式文件和 `operation_log.jsonl`；CEO 摘要只在对话中提供，不写入目录。
不要改写 input 或 materials.zip；不要编造推荐或 CEO 决定。
```

### 模板 B：从已交付的 `materials.zip` 开始

收到材料包 ZIP 时使用本模板。不要再次调用 Material Pack Agent，不要解压后改写 ZIP 内文件。若没有同一运行的 `candidate_process_register.xlsx` 和 `run_control.json`，停止并请求完整受控交接；仅有 ZIP 不能启动 Candidate Pack。

```text
使用 $candidate-pack-agent 处理 <CURRENT_OUTPUT_DIR>。
仅读取该目录中同一运行的 materials.zip、candidate_process_register.xlsx、run_control.json、可选 base_intake_observations.json 和受控导出的可选 follow_up_resolution.json；不要读取历史 output、参考输出、临时文件或其他目录。Candidate Pack 在读取业务数据前复算 ZIP、登记表、Base 观察（如存在）及当前插件 manifest 的 SHA-256/字节数，并与 run_control.json 的同次交接摘要逐项匹配；任何不匹配、缺少绑定或插件版本不一致均要求从授权源重新构建。额外文件、嵌套目录、符号链接或结构争议均按当前固定契约阻断，不因对话中的临时要求放宽。
对于 Base 交接，材料门禁关闭后先进入 HRD 审核意见流程。HRD 意见确认保存后，使用 `build_ceo_package.py` 重新比对当前 Base 的材料与流程元数据，再生成仅包含已保存 HRD 意见的正式 CEO 包；无已保存意见时不生成空包。不要发送到飞书，直到我明确确认收件人和摘要原文。
```

### 模板 C：HRD 审核意见

仅当同一输出目录的材料门禁已关闭后使用。先请求 `$candidate-pack-agent` 调用 `manage_hrd_review.py queue`，只展示 HRD 面试通过候选人及已保存意见状态。HRD 可以自然表达一个或多个意见；先逐条提取候选人、`建议录用` 或 `不建议录用` 与至少一句理由，并展示待保存摘要。候选人、建议方向或理由不明确时只追问缺失内容，不得保存、猜测或代填。

在 HRD 审阅摘要后，调用 `manage_hrd_review.py propose` 将已展示的结构化意见写入同一 output 的临时提案。只有 HRD 对同一提案明确确认保存后，才调用 `manage_hrd_review.py confirm --confirm SAVE`。确认步骤必须以当前已授权用户身份重新验证 Base、字段类型和 HRD 面试通过资格，写入 `HRD审核意见`、`HRD意见状态`、`HRD录用建议` 后再读回验证。绝不创建、修改或推断 `CEO最终决策`，也不要因 HRD 意见保存而发送 CEO 包。

### 模板 D：CEO 包

```text
使用 scripts/build_ceo_package.py 处理 <CURRENT_OUTPUT_DIR> 与 <BASE_URL>。
只读取当前 output 和当前授权 Base；先重新比较受控交接的材料与流程元数据。仅把当前仍为 HRD 面试通过且已填写 HRD 审核意见、意见状态和录用建议的候选人纳入包。若元数据变化、材料门禁未关闭、候选人不明确或无已保存意见，停止且不生成空包。不得写入 CEO最终决策。
```

`<WORKSPACE_ROOT>` 是本次新建的工作目录。除非任务明确要求同一批次追加处理，否则不要复用旧的 `output/` 目录。

## 六、首次飞书配置

飞书投递是可选步骤，也是唯一需要 Python 与 `lark-cli` 的步骤。每位发送者必须在自己的 Mac 上完成一次配置。严禁将 App Secret 或 token 放入 Codex 对话、材料目录、发布包或 `output/`。

发送者或管理员应提供组织已授权的飞书自建应用 App ID。发送者在 Terminal 中自行输入 App Secret，并自行完成浏览器或设备授权：

```bash
npx @larksuite/cli@latest install
lark-cli profile add --name <PROFILE_NAME> --app-id <APP_ID> --app-secret-stdin --brand feishu
lark-cli --profile <PROFILE_NAME> auth login --scope "im:message im:message.send_as_user im:resource contact:user:search offline_access"
python3 scripts/feishu_delivery.py preflight --recipient email:person@example.com --lark-profile <PROFILE_NAME>
```

`npx` 仅用于安装 `lark-cli`，该 Mac 可能需要先具备 Node.js；它不是材料整理流程的依赖。若收件人使用 `open_id:` 或 `chat_id:`，授权 scope 中可移除 `contact:user:search`。飞书应用必须已获同样的 scope，并对该发送者在对应租户中可用。

首次配置时，可将以下内容直接发送给 Codex：

```text
请为飞书投递执行环境检查：确认 python3 版本至少为 3.9 且 lark-cli 可用。
如 lark-cli 缺失，按说明文档给出官方安装命令并在我批准后执行。
使用本机 profile <PROFILE_NAME>，不要切换默认 profile。
我会在终端中自行完成 App Secret 输入和用户授权。授权完成后，只运行 preflight，
收件人为 <email:person@example.com 或 open_id:ou_xxx 或 chat_id:oc_xxx>；不要读取输出文件，不要上传，也不要发送。
```

## 七、飞书预览与发送模板

先要求 Codex 只做预检和预览：

```text
使用本插件的 scripts/feishu_delivery.py 对 <WORKSPACE_ROOT>/output 进行飞书预检与 preview。
发送者 profile 为 <PROFILE_NAME>，收件人为 <EXPLICIT_RECIPIENT>。
不要发送；先向我展示 CEO 摘要原文、Batch ID、两个附件的 SHA-256 和 delivery_fingerprint。
```

审核完上述内容后，必须在同一 Codex 对话中明确确认收件人和 CEO 摘要原文，再发送：

```text
检查确认完毕。请使用已经展示且未变化的 CEO 摘要、Batch ID 和 delivery_fingerprint，
通过飞书发送给 <EXPLICIT_RECIPIENT>。只发送摘要、candidate_pack.html 和 materials.zip。
```

插件以已授权的飞书用户身份发送，绝不以应用机器人身份回退发送。文件、摘要、确认、scope、收件人解析或输出目录边界任一不符合要求时，插件必须阻断发送。

## 八、环境检查与常见阻断

| 检查项或阻断 | Codex 应执行的动作 |
| --- | --- |
| `codex: command not found` | 安装或更新 Codex 并登录，再执行 `codex --version`。IDE extension 不能安装此插件。 |
| 插件已安装但 Skill 不可用 | 确认 `installed: true` 与 `enabled: true`，然后新开 Codex 对话。 |
| `python3` 缺失或低于 3.9 | 安装组织允许的 macOS Python 3.9+，重开 Terminal 后执行 `python3 --version`。仅飞书投递需要。 |
| `lark-cli: command not found` | 在 Node.js 可用后执行 `npx @larksuite/cli@latest install`，重开 Terminal 后执行 `lark-cli --version`。 |
| `preflight` 提示缺少 scope 或用户身份未就绪 | 按错误中列出的 scope 重新授权当前用户；不得改用他人 profile 或机器人。 |
| 邮箱无法唯一解析 | 改用明确的 `open_id:ou_xxx` 或 `chat_id:oc_xxx`；不得按显示名称猜测。 |
| preview 提示输出文件多余或缺失 | `output/` 只能有六个正式文件和 `operation_log.jsonl`；已发生部分发送时允许同目录受控 `delivery_receipt.json`。`.DS_Store` 被忽略，其他额外文件都会阻断发送。 |
| 文件超过 30 MB | 不得自动拆分、重压缩或改写 `materials.zip`；应报告阻断并取得新的已批准投递路径。 |
| 来源批次超过 2 GB、音频超过 256 MB 或 60 分钟 | 不得读取、下载、拆分、转码或降低限制；使用新的授权批次或获得已批准的替代流程。 |
| 磁盘空间不足、Base 附件无可验证大小、ZIP 成员数/展开体积/压缩比超限 | 停止当前运行；不得复用部分输出或绕过预检。 |

## 九、测试者检查清单

- [ ] 发布 ZIP 的 SHA-256 与同名 `.sha256` 文件一致。
- [ ] 解压后执行 `shasum -a 256 -c SHA256SUMS.txt`，发布包内文件均通过校验。
- [ ] `codex plugin list --marketplace recruiting-evidence-agents-v0-5-0-marketplace --json` 显示已安装且启用。
- [ ] 新开的 Codex 对话可调用四个业务 Skills。
- [ ] 收到的 `materials.zip` 未被编辑，也未与旧输出混放。
- [ ] Base-to-CEO 输出严格只有六个正式文件和 `operation_log.jsonl`。
- [ ] 首次飞书发送前，本机发送者已完成 profile 配置和授权。
- [ ] 每次飞书发送前，用户都审核了 CEO 摘要原文、明确收件人、Batch ID 和 delivery fingerprint。

## 十、安全与隐私规则

- 不要在发布包或说明中放入候选人数据、App Secret、访问 token、CLI profile 或收件人别名。
- 不要在不同 Mac 之间复制 `lark-cli` 配置；token 归属和收件人权限属于本机发送者。
- 不得把 `Not Reached`、`Decision Blocked`、`Not Provided` 或 `Unknown` 解释为淘汰或通过。
- 飞书只发送 CEO 摘要、`candidate_pack.html` 和 `materials.zip`；其余三个 Markdown 文件留在本地。
