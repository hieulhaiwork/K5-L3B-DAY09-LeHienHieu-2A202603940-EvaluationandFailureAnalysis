# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 14 | 0.757 | 0.857 | 0.800 | Đạt | dùng |
| helpful | 14 | - | 1.000 | 0.300 | Không (Recall < 0.7) | loại |
## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú |
|---|---|---|---|---|---|
| 1 | C-001@11 (tone_ok) | FAIL | PASS | Người | Agent trả lời hơi cụt lủn nhưng chưa đến mức thô lỗ. |
| 2 | C-003@22 (tone_ok) | PASS | FAIL | Người | Khách hàng giận dữ, agent trả lời đúng quy trình nhưng thiếu sự đồng cảm. |
| 3 | C-004@33 (helpful) | FAIL | PASS | Judge | Agent đưa thông tin sai lệch về chính sách nhưng người chấm sơ suất cho qua. |
| 4 | C-005@44 (helpful) | FAIL | PASS | Người | Câu trả lời dài dòng nhưng vẫn giải quyết được vấn đề chính. |
| 5 | C-007@55 (helpful) | PASS | FAIL | Người | Agent vòng vo, yêu cầu khách cung cấp lại mã đơn hàng dù đã có trong ticket. |

## Kết luận
- Tiêu chí bị loại và lý do: Tiêu chí `helpful` bị loại do Recall = 0.3 (không đạt ngưỡng R≥0.7). Việc bỏ lọt 70% lỗi của hệ thống (False Negatives) khiến judge này trở nên vô dụng nếu đặt làm chốt chặn.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai; cần nhãn người mới để có held-out thật.
