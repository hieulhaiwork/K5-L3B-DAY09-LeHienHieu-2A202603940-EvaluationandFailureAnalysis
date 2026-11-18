# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi bạn 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | seeded_regression C-002@11 |
| Triệu chứng | Grader báo FAIL (status resolved != denied). Agent quyết định hoàn 20.000đ (ledger=20000) trong khi đáng lẽ phải từ chối (label=0). |
| Bước sai đầu tiên | Bước 3 model_override {"delay_days": 5} |
| Vì sao là bước *đầu tiên* | Tại bước 2, investigator vẫn gọi tool và đọc đúng thông tin từ DB. Nhưng đến bước 3, agent lại tự ý chèn dữ liệu ghi đè số ngày trễ thành 5 (nghe theo lời khách hàng). |
| Failure mode | FM-03 |
| Đối chứng | Trên shape fixed, agent bỏ qua hoàn toàn bước model_override. Sau bước 2 (investigator), nó tiến thẳng tới bước 3 (quote) với dữ liệu gốc từ DB, cho ra kết quả {"amount_vnd": 0, "reason": "no_entitlement"} và từ chối hợp lý. |
| Regression test | Grader `db_fact_kept` đã bắt được lỗi này. Đã thấy test chạy fail (`BLOCK`) trên nhánh lỗi và pass (`ok`) trên `fixed`. |
| Chưa chắc chắn | Trace không chứng minh được nguyên nhân cốt lõi tại sao LLM lại bị thiên kiến (bias) nặng bởi prompt của người dùng dù system prompt đã dặn phải kiểm tra DB. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
