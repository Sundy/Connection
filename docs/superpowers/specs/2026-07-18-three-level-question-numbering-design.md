# 三级题号与卷面批注设计

## 背景与根因

`FAM-000001` 的真实作业页包含印刷主题号 1–14，其中第 12–14 题含多个小题。使用修正后的 JSON Object 请求重放时，`qwen-vl-plus` 返回了 22 个叶子小题，题号包括 `一、1`、`四、12(1)` 等。

现有 `normalize_question_no()` 只匹配字符串开头，导致 `一、1`、`一、2`、`一、3` 都被归为 `一`，而 `四、12(1)` 到 `四、14(2)` 可能都被归为 `四`。模型识别到了题目，但本地归一化错误地合并了不同主题题。

## 目标

- 支持试卷的章节、主题题、小题三级编号。
- 页面仍以 14 个主题题为主要展示粒度。
- 主题题内部保留每个小题的答案、正误、解释、置信度和卷面批注。
- 批注的原图页面、归属小题和归一化坐标在聚合过程中不丢失或串题。
- 识别到主题题编号缺口时，不静默显示为完整批改，而是标记家长复核。

## 数据模型

`question_results` 继续保存叶子级批改结果，并增加两个可空字符串字段：

- `section_no`：一级章节编号，例如 `一`、`二`、`四`。
- `question_no`：二级主题题号，例如 `1`、`12`、`14`。现有字段保留并改为只保存主题题号。
- `subquestion_no`：三级小题号，例如 `1`、`2`；主题题没有小题时为空。

示例 `四、12(3)` 保存为 `section_no="四"`、`question_no="12"`、`subquestion_no="3"`。同一主题题的多个叶子结果按 `(source_media_id, section_no, question_no)` 聚合。

MySQL 通过幂等结构检查增加 `section_no` 和 `subquestion_no`。两个字段允许为空，因此历史结果保持可读，不批量重写历史记录。

## AI 请求与兼容解析

Vision 提示词要求：

- 从上到下检查全部印刷题号，不跳过选择题、填空题、计算题或答案写在页边的题目。
- 每个叶子小题返回 `section_no`、`question_no`、`subquestion_no`。
- `section_no` 只包含章节编号，`question_no` 只包含主题题号，`subquestion_no` 只包含小题号。
- 每个叶子小题独立返回答案、正误、解释、置信度和 annotations。
- 继续使用 `response_format={"type": "json_object"}` 保证合法 JSON。

兼容解析器同时接受结构化字段和旧组合格式：

- `一、1` → `section_no="一"`、`question_no="1"`、`subquestion_no=None`
- `四、12(3)` → `section_no="四"`、`question_no="12"`、`subquestion_no="3"`
- `12(3)` → `section_no=None`、`question_no="12"`、`subquestion_no="3"`
- `第12题（3）` → `section_no=None`、`question_no="12"`、`subquestion_no="3"`

若模型已提供拆分字段，以拆分字段为准；组合 `question_no` 仅作为兼容回退。

## 聚合与状态

数据库保存叶子小题，API 按主题题聚合并返回嵌套 `subquestions`：

```json
{
  "section_no": "四",
  "question_no": "12",
  "is_correct": false,
  "subquestions": [
    {"subquestion_no": "1", "is_correct": true},
    {"subquestion_no": "2", "is_correct": false},
    {"subquestion_no": "3", "is_correct": true}
  ]
}
```

主题题状态采用稳定优先级：

1. 任一叶子小题为错误，主题题为错误。
2. 没有错误但任一叶子小题待复核，主题题待复核。
3. 所有叶子小题正确，主题题正确。

没有小题的主题题自身就是唯一叶子结果。主题题摘要和页面正确/错误/待复核列表去重后只显示一次主题题号。

## 漏题检测

每页完成题号解析后，对连续阿拉伯主题号执行覆盖检查。若同一份结果的最大主题号为 14，但 1–14 中存在缺号，则：

- `needs_review` 设为 true。
- `review_reason` 追加明确缺失题号，例如“未生成第 4、7 题批改结果”。
- 不伪造缺失题目的答案或正误。

章节内编号重新从 1 开始时，以 `(section_no, question_no)` 区分主题题；只有可明确判断为连续全局编号的页面才执行 1–最大题号检查，避免把章节重编号误报为漏题。

## 卷面批注链路

annotations 继续保存在叶子级 `QuestionResult.annotations_json`，每条记录保留对应的 `source_media_id`。API 聚合主题题时：

- 每个小题在 `subquestions` 中保留自己的 annotations。
- 主题题同时提供所有叶子批注的扁平合集，供现有整页覆盖层绘制。
- 不重新计算模型返回的相对坐标，不跨页面移动批注。
- 低于 `annotation_confidence_threshold` 的批注继续过滤。
- 待复核小题继续移除结论性勾、圈、叉，只保留中性文字批注。

小程序覆盖层使用主题题的扁平 annotations 绘制，因此第 12(1) 和第 12(2) 的圈可以同时显示在原图各自位置；详情列表使用嵌套小题展示各自结论。

代码能保证批注归属和坐标传递正确，但模型坐标本身的视觉精度仍受照片清晰度、倾斜和遮挡影响。

## API 与小程序兼容

结果 API 的顶层 `questions` 和每页 `questions` 改为主题题列表，并增加 `section_no` 与 `subquestions`。保留现有主题题字段，单层旧结果返回 `subquestions=[]`，避免历史结果无法展示。

学生和家长结果页继续显示主题题卡片；有小题时在卡片内逐项显示 `(1)`、`(2)` 的正误、答案和解释。整页批注组件继续从主题题 annotations 扁平列表绘制。

## 错误处理与测试

测试覆盖：

- 本次真实模型题号列表可解析为 1–14 共 14 个主题题。
- 三级格式和旧组合格式的解析。
- 小题状态按错误、待复核、正确优先级聚合。
- annotations 在小题和主题题扁平合集内均完整保留。
- 跨页面不串批注。
- 全局连续编号缺号时标记复核。
- 章节重新编号时不误报缺号。
- 历史单层 `QuestionResult` 仍能序列化和展示。

## 非目标

- 不承诺模型在模糊、裁切或严重倾斜照片上的坐标绝对精确。
- 不自动重跑或改写已有失败提交。
- 不重写历史 `question_results` 数据。
- 不引入独立的章节表、主题题表或小题表。
