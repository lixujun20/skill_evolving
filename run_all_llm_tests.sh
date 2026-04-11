#!/usr/bin/env bash
# run_all_llm_tests.sh
# 串行运行所有 LLM tests（tester + extractor dim1-6），保持 DB isolation
# 用 LLM_TRACE_FILE 记录完整 LLM 输入输出（截断消息内容，避免日志膨胀）
# 输出: /tmp/full_llm_run_<timestamp>.log  (pytest 摘要)
#       /tmp/llm_trace_<timestamp>.log      (LLM 输入输出详情)

set -euo pipefail

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOGFILE="/tmp/full_llm_run_${TIMESTAMP}.log"
TRACEFILE="/tmp/llm_trace_${TIMESTAMP}.log"
TESTDIR="app/meta_agent/skills/tests"

cd /home/lixujun/AICosmos

echo "========================================"  | tee -a "$LOGFILE"
echo "LLM 完整测试运行: $(date)"                | tee -a "$LOGFILE"
echo "pytest 摘要日志: $LOGFILE"                | tee -a "$LOGFILE"
echo "LLM 详细 trace: $TRACEFILE"              | tee -a "$LOGFILE"
echo "========================================"  | tee -a "$LOGFILE"

run_dim() {
    local label="$1"
    local path="$2"
    printf "\n" | tee -a "$LOGFILE"
    echo "######## $label ########" | tee -a "$LOGFILE"
    echo "开始时间: $(date)" | tee -a "$LOGFILE"
    # LLM_TRACE_FILE: 将 LLM 输入输出写入独立 trace 文件（不写 stdout，避免 pytest 捕获后指数膨胀）
    # --no-capture-output: conda 直接透传子进程 stdout/stderr
    # --timeout=600: 单测试最多 10 分钟
    if LLM_TRACE_FILE="$TRACEFILE" conda run -n meta-agent --no-capture-output \
        PYTHONPATH=. pytest -m llm -v --tb=short --timeout=600 "$path" >> "$LOGFILE" 2>&1; then
        echo "[$label] ALL PASSED  $(date)" | tee -a "$LOGFILE"
    else
        echo "[$label] SOME FAILED  $(date)" | tee -a "$LOGFILE"
    fi
    echo "结束时间: $(date)" | tee -a "$LOGFILE"
}

# ---------- Tester suite (dim1-6) ----------
run_dim "Tester dim1-6" "$TESTDIR/tester/"

# ---------- Extractor dim1-6 ----------
run_dim "Extractor dim1" "$TESTDIR/extractor/test_dim_1_extractor.py"
run_dim "Extractor dim2" "$TESTDIR/extractor/test_dim_2_extractor.py"
run_dim "Extractor dim3" "$TESTDIR/extractor/test_dim_3_extractor.py"
run_dim "Extractor dim4" "$TESTDIR/extractor/test_dim_4_extractor.py"
run_dim "Extractor dim5" "$TESTDIR/extractor/test_dim_5_extractor.py"
run_dim "Extractor dim6" "$TESTDIR/extractor/test_dim_6_extractor.py"

echo "" | tee -a "$LOGFILE"
echo "========================================"  | tee -a "$LOGFILE"
echo "全部测试完成: $(date)"                     | tee -a "$LOGFILE"
echo "pytest 摘要: $LOGFILE"                     | tee -a "$LOGFILE"
echo "LLM trace:   $TRACEFILE"                   | tee -a "$LOGFILE"
echo "========================================"  | tee -a "$LOGFILE"

# 汇总
echo "" | tee -a "$LOGFILE"
echo "-------- 汇总 --------" | tee -a "$LOGFILE"
grep -E "ALL PASSED|SOME FAILED|###" "$LOGFILE" | tee -a /dev/null
