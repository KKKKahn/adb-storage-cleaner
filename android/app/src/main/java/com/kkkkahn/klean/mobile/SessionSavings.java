package com.kkkkahn.klean.mobile;

import java.util.HashMap;
import java.util.Map;

final class SessionSavings {
    private static final Map<String, Long> live = new HashMap<>();
    private static final Map<String, Long> video = new HashMap<>();

    private SessionSavings() {
    }

    static synchronized void applied(String path, boolean liveItem, long savedBytes) {
        if (savedBytes <= 0) return;
        (liveItem ? live : video).put(path, savedBytes);
    }

    static synchronized void restored(String path) {
        live.remove(path);
        video.remove(path);
    }

    static synchronized long bytes(boolean liveMode) {
        long total = 0;
        for (long value : (liveMode ? live : video).values()) total += value;
        return total;
    }
}
