package com.kkkkahn.klean.mobile;

final class TrialOutcome {
    final boolean success;
    final boolean profitable;
    final String reason;
    final long sourceBytes;
    final long outputBytes;
    final long contentDurationMs;
    final long elapsedMs;
    final boolean hardware;
    final boolean uuidPreserved;
    final String encoderName;
    final String outputPath;

    private TrialOutcome(
            boolean success,
            boolean profitable,
            String reason,
            long sourceBytes,
            long outputBytes,
            long contentDurationMs,
            long elapsedMs,
            boolean hardware,
            boolean uuidPreserved,
            String encoderName,
            String outputPath) {
        this.success = success;
        this.profitable = profitable;
        this.reason = reason;
        this.sourceBytes = sourceBytes;
        this.outputBytes = outputBytes;
        this.contentDurationMs = contentDurationMs;
        this.elapsedMs = elapsedMs;
        this.hardware = hardware;
        this.uuidPreserved = uuidPreserved;
        this.encoderName = encoderName;
        this.outputPath = outputPath;
    }

    static TrialOutcome success(Transcoder.Result result, String outputPath) {
        return new TrialOutcome(true, result.profitable, "", result.sourceBytes,
                result.outputBytes, result.contentDurationMs, result.elapsedMs,
                result.hardware, result.uuidPreserved, result.encoderName, outputPath);
    }

    static TrialOutcome failed(String reason) {
        return new TrialOutcome(false, false, reason, 0, 0, 0, 0,
                false, false, "", "");
    }

    int savedPercent() {
        if (!success || sourceBytes <= 0 || outputBytes <= 0) return 0;
        return (int) Math.round((sourceBytes - outputBytes) * 100.0 / sourceBytes);
    }

    double realtimeFactor() {
        if (!success || elapsedMs <= 0 || contentDurationMs <= 0) return 0;
        return (contentDurationMs / 1000.0) / (elapsedMs / 1000.0);
    }
}
