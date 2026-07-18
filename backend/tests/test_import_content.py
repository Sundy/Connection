from backend.app.core.config import Settings
from backend.app.models import AssignmentBatch, ImportFile


def test_import_intelligence_fields_and_thresholds_exist():
    file = ImportFile(
        import_batch_id=1,
        file_name="tmp_123.png",
        file_type="image",
        file_url="/tmp/tmp_123.png",
        document_role="homework",
        recognized_title="数学四年级下册第3单元练习",
        recognition_status="success",
        match_status="not_required",
    )
    plan = AssignmentBatch(student_id=1, title="新增作业", target_assignment_batch_id=8)

    assert file.document_role == "homework"
    assert file.recognized_title == "数学四年级下册第3单元练习"
    assert plan.target_assignment_batch_id == 8
    assert Settings().import_title_confidence_threshold == 0.75
    assert Settings().answer_match_confidence_threshold == 0.80
