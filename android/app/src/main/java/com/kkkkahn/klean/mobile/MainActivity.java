package com.kkkkahn.klean.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DatePickerDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.StatFs;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.GridView;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class MainActivity extends Activity {
    private static final String TAG = "KleanMobile";
    private static final int REQUEST_ALL_FILES = 100;
    private static final int REQUEST_STORAGE = 101;
    private static final int REQUEST_BACKUP = 102;
    private static final String[] QUALITY_LABELS = {
            "高画质 · 约省 35%", "推荐 · 约省 50%", "小体积 · 约省 62%"
    };
    private static final double[] QUALITY_FACTORS = {0.62d, 0.50d, 0.38d};
    private static final String SETTINGS = "klean_settings";
    private static final Pattern NAME_DATE =
            Pattern.compile("(\\d{4})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})");

    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Set<String> selected = new LinkedHashSet<>();
    private final Map<String, TrialOutcome> outcomes = new HashMap<>();
    private final Map<String, Storage.HistoryEntry> history = new HashMap<>();
    private final AtomicBoolean cancelRequested = new AtomicBoolean(false);

    private FrameLayout root;
    private ThumbnailLoader thumbnails;
    private MediaAdapter adapter;
    private MediaScanner.Result scanResult;
    private final List<MediaItem> liveItems = new ArrayList<>();
    private final List<MediaItem> videoItems = new ArrayList<>();
    private final List<MediaItem> displayedItems = new ArrayList<>();
    private int tab;
    private String statusFilter = "all";
    private boolean sortBySize;
    private long rangeStart;
    private long rangeEnd;
    private int qualityIndex = 1;
    private Job currentJob;
    private final Set<String> applySelection = new LinkedHashSet<>();
    private boolean pendingRescan;

    private boolean mainBuilt;
    private boolean scanning;
    private boolean busy;
    private boolean applying;

    private TextView liveValue;
    private TextView videoValue;
    private TextView compressedValue;
    private TextView tabLive;
    private TextView tabVideo;
    private TextView selectAllButton;
    private TextView statusAll;
    private TextView statusUncompressed;
    private TextView statusCompressed;
    private TextView dateButton;
    private TextView sortButton;
    private TextView hint;
    private TextView emptyView;
    private TextView selectionInfo;
    private TextView selectionHint;
    private TextView compressButton;
    private TextView previewButton;
    private TextView clearButton;
    private TextView progressText;
    private ProgressBar progressBar;
    private View progressPanel;
    private DragSelectGridView listView;
    private TextView scanButton;
    private TextView backupButton;
    private TextView resultApplyButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Storage.clearOldJobs(this);
        qualityIndex = getSharedPreferences(SETTINGS, MODE_PRIVATE)
                .getInt("quality", 1);
        thumbnails = new ThumbnailLoader(this);
        root = new FrameLayout(this);
        root.setBackgroundResource(R.drawable.bg_app);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            getWindow().setStatusBarColor(Color.TRANSPARENT);
            getWindow().setNavigationBarColor(Color.TRANSPARENT);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(
                        android.view.WindowInsets.Type.systemBars());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
        }
        setContentView(root);
        if (hasStorageAccess()) showMain();
        else showPermission();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!hasStorageAccess()) {
            if (mainBuilt) {
                mainBuilt = false;
                showPermission();
            }
            return;
        }
        if (!mainBuilt) {
            showMain();
        } else if (pendingRescan && !busy && !scanning) {
            pendingRescan = false;
            startScan();
        } else if (scanResult == null && !scanning && !busy) {
            startScan();
        }
    }

    @Override
    public void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_BACKUP && resultCode == RESULT_OK) {
            pendingRescan = false;
            if (mainBuilt && !busy && !scanning) startScan();
        }
    }

    @Override
    protected void onDestroy() {
        cancelRequested.set(true);
        io.shutdownNow();
        thumbnails.shutdown();
        super.onDestroy();
    }

    private boolean hasStorageAccess() {
        if (Build.VERSION.SDK_INT >= 30) {
            return Environment.isExternalStorageManager();
        }
        return checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED
                && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void showPermission() {
        root.removeAllViews();
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(Gravity.CENTER);
        column.setPadding(Ui.dp(this, 30), Ui.dp(this, 40),
                Ui.dp(this, 30), Ui.dp(this, 30));
        column.setBackgroundResource(R.drawable.bg_app);
        root.addView(column, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        column.addView(Ui.logo(this, 76));
        TextView title = Ui.text(this, "Klean Mobile", 27f, Ui.TEXT, true);
        title.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        titleParams.topMargin = Ui.dp(this, 18);
        column.addView(title, titleParams);

        TextView sub = Ui.text(this,
                "在手机本地压缩实况照片与普通视频\n原片自动备份、可预览、可恢复",
                12.5f, Ui.MUTED, false);
        sub.setGravity(Gravity.CENTER);
        sub.setLineSpacing(Ui.dp(this, 3), 1f);
        LinearLayout.LayoutParams subParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        subParams.topMargin = Ui.dp(this, 9);
        column.addView(sub, subParams);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(Ui.dp(this, 18), Ui.dp(this, 16),
                Ui.dp(this, 18), Ui.dp(this, 16));
        card.setBackground(Ui.bordered(Ui.CARD, 14f, Ui.BORDER, this));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        cardParams.topMargin = Ui.dp(this, 30);
        column.addView(card, cardParams);
        card.addView(Ui.text(this, "需要「所有文件访问」", 14f, Ui.TEXT, true));
        TextView why = Ui.text(this,
                "Android 11 以后，读取相册原片、备份并在确认后替换，需要这个权限。"
                        + "Klean 只处理你选中的媒体文件，压缩结果会先给你预览，确认后才替换原片。",
                11.5f, Ui.MUTED, false);
        why.setLineSpacing(Ui.dp(this, 3), 1f);
        LinearLayout.LayoutParams whyParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        whyParams.topMargin = Ui.dp(this, 8);
        card.addView(why, whyParams);

        TextView grant = Ui.button(this, "授予所有文件访问", true);
        grant.setTextSize(14f);
        grant.setPadding(Ui.dp(this, 20), Ui.dp(this, 13),
                Ui.dp(this, 20), Ui.dp(this, 13));
        grant.setOnClickListener(v -> requestStorageAccess());
        LinearLayout.LayoutParams grantParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        grantParams.topMargin = Ui.dp(this, 26);
        column.addView(grant, grantParams);

        TextView note = Ui.text(this,
                "Android 10 会改为申请存储读写权限。\n所有处理都在本机完成，不联网、不上传。",
                10.5f, Ui.MUTED, false);
        note.setGravity(Gravity.CENTER);
        note.setLineSpacing(Ui.dp(this, 2), 1f);
        LinearLayout.LayoutParams noteParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        noteParams.topMargin = Ui.dp(this, 18);
        column.addView(note, noteParams);
    }

    private void requestStorageAccess() {
        if (Build.VERSION.SDK_INT >= 30) {
            try {
                startActivityForResult(new Intent(
                                Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                Uri.parse("package:" + getPackageName())),
                        REQUEST_ALL_FILES);
            } catch (Exception first) {
                try {
                    startActivityForResult(
                            new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION),
                            REQUEST_ALL_FILES);
                } catch (Exception second) {
                    Toast.makeText(this, "请在系统设置中授予所有文件访问权限",
                            Toast.LENGTH_LONG).show();
                }
            }
        } else {
            requestPermissions(new String[]{
                    Manifest.permission.READ_EXTERNAL_STORAGE,
                    Manifest.permission.WRITE_EXTERNAL_STORAGE
            }, REQUEST_STORAGE);
        }
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_STORAGE) {
            if (hasStorageAccess()) showMain();
            else Toast.makeText(this, "没有存储权限，无法读取媒体文件",
                    Toast.LENGTH_LONG).show();
        }
    }

    private void showMain() {
        if (mainBuilt) return;
        mainBuilt = true;
        root.removeAllViews();
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundResource(R.drawable.bg_app);
        root.addView(page, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        page.addView(buildHeader());
        page.addView(buildSummary());
        page.addView(buildTabs());
        page.addView(buildFilters());
        page.addView(buildHint());

        FrameLayout listWrap = new FrameLayout(this);
        page.addView(listWrap, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        listView = new DragSelectGridView(this);
        listView.setNumColumns(3);
        listView.setStretchMode(GridView.STRETCH_COLUMN_WIDTH);
        listView.setHorizontalSpacing(Ui.dp(this, 6));
        listView.setVerticalSpacing(Ui.dp(this, 6));
        listView.setBackgroundColor(Color.TRANSPARENT);
        listView.setCacheColorHint(Color.TRANSPARENT);
        listView.setPadding(Ui.dp(this, 12), Ui.dp(this, 6),
                Ui.dp(this, 12), Ui.dp(this, 12));
        listView.setClipToPadding(false);
        listView.setVerticalScrollBarEnabled(false);
        adapter = new MediaAdapter(this, thumbnails, selected, outcomes,
                this::updateSelection);
        listView.setAdapter(adapter);
        listView.setOnItemClickListener((parent, view, position, id) -> {
            if (busy || scanning) return;
            toggleItem(adapter.getItem(position));
        });
        listView.setDragListener(new DragSelectGridView.Listener() {
            @Override
            public void onDragStart(int position) {
                if (busy || scanning) return;
                selected.add(adapter.getItem(position).path);
                adapter.refresh();
                updateSelection();
            }

            @Override
            public void onDragOver(int position) {
                if (busy || scanning) return;
                if (selected.add(adapter.getItem(position).path)) {
                    adapter.refresh();
                    updateSelection();
                }
            }

            @Override
            public void onDragEnd() {
                adapter.refresh();
                updateSelection();
            }
        });
        TextView empty = Ui.text(this, "正在扫描媒体库…", 13f, Ui.MUTED, false);
        empty.setGravity(Gravity.CENTER);
        empty.setPadding(0, Ui.dp(this, 50), 0, 0);
        listWrap.addView(listView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        listWrap.addView(empty, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        listView.setEmptyView(empty);
        emptyView = empty;
        page.addView(buildProgress());
        page.addView(buildBottomBar());
        updateTabs();
        updateSelection();
        showEmptyState();
        startScan();
    }

    private View buildHeader() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(Ui.dp(this, 18), Ui.dp(this, 10),
                Ui.dp(this, 18), Ui.dp(this, 4));
        header.addView(Ui.logo(this, 32));
        LinearLayout titleColumn = new LinearLayout(this);
        titleColumn.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        titleParams.setMarginStart(Ui.dp(this, 11));
        header.addView(titleColumn, titleParams);
        titleColumn.addView(Ui.text(this, "Klean Mobile", 18f, Ui.TEXT, true));
        TextView sub = Ui.text(this, "本地压缩 · 原片自动备份 · 不上传",
                9.5f, Ui.MUTED, false);
        sub.setPadding(0, Ui.dp(this, 3), 0, 0);
        titleColumn.addView(sub);

        backupButton = Ui.button(this, "备份", false);
        backupButton.setTextSize(11.5f);
        backupButton.setPadding(Ui.dp(this, 11), Ui.dp(this, 7),
                Ui.dp(this, 11), Ui.dp(this, 7));
        backupButton.setOnClickListener(v -> openBackups());
        LinearLayout.LayoutParams backupParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        backupParams.setMarginEnd(Ui.dp(this, 6));
        header.addView(backupButton, backupParams);

        scanButton = Ui.button(this, "扫描", false);
        scanButton.setTextSize(11.5f);
        scanButton.setPadding(Ui.dp(this, 11), Ui.dp(this, 7),
                Ui.dp(this, 11), Ui.dp(this, 7));
        scanButton.setOnClickListener(v -> {
            if (!busy && !scanning) startScan();
        });
        header.addView(scanButton);
        return header;
    }

    private View buildSummary() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.HORIZONTAL);
        card.setGravity(Gravity.CENTER_VERTICAL);
        card.setPadding(Ui.dp(this, 14), Ui.dp(this, 9),
                Ui.dp(this, 14), Ui.dp(this, 9));
        card.setBackground(Ui.bordered(Ui.CARD, 14f, Ui.BORDER, this));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        cardParams.setMargins(Ui.dp(this, 18), Ui.dp(this, 4),
                Ui.dp(this, 18), 0);
        card.setLayoutParams(cardParams);
        liveValue = addStat(card, "–", "实况照片");
        addDivider(card);
        videoValue = addStat(card, "–", "普通视频");
        addDivider(card);
        compressedValue = addStat(card, "0", "已压缩");
        return card;
    }

    private TextView addStat(LinearLayout parent, String value, String label) {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(Gravity.CENTER_HORIZONTAL);
        parent.addView(column, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView valueView = Ui.text(this, value, 17f, Ui.TEXT, true);
        valueView.setGravity(Gravity.CENTER);
        column.addView(valueView);
        TextView labelView = Ui.text(this, label, 9f, Ui.MUTED, false);
        labelView.setGravity(Gravity.CENTER);
        labelView.setPadding(0, Ui.dp(this, 3), 0, 0);
        column.addView(labelView);
        return valueView;
    }

    private void addDivider(LinearLayout parent) {
        View divider = new View(this);
        divider.setBackgroundColor(Ui.BORDER);
        parent.addView(divider, new LinearLayout.LayoutParams(
                Ui.dp(this, 1), Ui.dp(this, 28)));
    }

    private View buildTabs() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rowParams.setMargins(Ui.dp(this, 18), Ui.dp(this, 8),
                Ui.dp(this, 18), 0);
        row.setLayoutParams(rowParams);

        tabLive = Ui.button(this, "实况照片 0", false);
        tabLive.setTextSize(11.5f);
        tabLive.setGravity(Gravity.CENTER);
        tabLive.setPadding(Ui.dp(this, 10), Ui.dp(this, 7),
                Ui.dp(this, 10), Ui.dp(this, 7));
        row.addView(tabLive, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        tabLive.setOnClickListener(v -> selectTab(0));

        tabVideo = Ui.button(this, "普通视频 0", false);
        tabVideo.setTextSize(11.5f);
        tabVideo.setGravity(Gravity.CENTER);
        tabVideo.setPadding(Ui.dp(this, 10), Ui.dp(this, 7),
                Ui.dp(this, 10), Ui.dp(this, 7));
        LinearLayout.LayoutParams videoParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        videoParams.setMarginStart(Ui.dp(this, 8));
        row.addView(tabVideo, videoParams);
        tabVideo.setOnClickListener(v -> selectTab(1));

        selectAllButton = Ui.button(this, "全选", false);
        selectAllButton.setTextSize(11f);
        selectAllButton.setPadding(Ui.dp(this, 11), Ui.dp(this, 7),
                Ui.dp(this, 11), Ui.dp(this, 7));
        LinearLayout.LayoutParams allParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        allParams.setMarginStart(Ui.dp(this, 8));
        row.addView(selectAllButton, allParams);
        selectAllButton.setOnClickListener(v -> toggleSelectAll());
        return row;
    }

    private View buildFilters() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(Ui.dp(this, 18), Ui.dp(this, 6),
                Ui.dp(this, 18), 0);
        statusAll = chip("全部");
        statusUncompressed = chip("未压缩");
        statusCompressed = chip("已压缩");
        dateButton = chip("日期");
        sortButton = chip("时间");
        addChip(row, statusAll);
        addChip(row, statusUncompressed);
        addChip(row, statusCompressed);
        addChip(row, dateButton);
        addChip(row, sortButton);
        statusAll.setOnClickListener(v -> setStatusFilter("all"));
        statusUncompressed.setOnClickListener(v -> setStatusFilter("uncompressed"));
        statusCompressed.setOnClickListener(v -> setStatusFilter("compressed"));
        dateButton.setOnClickListener(v -> chooseDateFilter());
        sortButton.setOnClickListener(v -> {
            if (busy || scanning) return;
            sortBySize = !sortBySize;
            applyFilters();
        });
        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        scroll.addView(row);
        return scroll;
    }

    private TextView chip(String text) {
        TextView view = Ui.text(this, text, 10.5f, Ui.MUTED, false);
        view.setGravity(Gravity.CENTER);
        view.setPadding(Ui.dp(this, 9), Ui.dp(this, 5),
                Ui.dp(this, 9), Ui.dp(this, 5));
        view.setClickable(true);
        view.setFocusable(true);
        styleChip(view, false);
        return view;
    }

    private void addChip(LinearLayout parent, TextView chip) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMarginEnd(Ui.dp(this, 6));
        parent.addView(chip, params);
    }

    private void styleChip(TextView view, boolean active) {
        view.setBackground(active
                ? Ui.bordered(0xFF242A48, 8f, Ui.ACCENT, this)
                : Ui.bordered(Ui.CARD, 8f, Ui.BORDER, this));
        view.setTextColor(active ? Ui.TEXT : Ui.MUTED);
    }

    private View buildHint() {
        hint = Ui.text(this, "压缩结果先预览，确认后才替换原片；原片会自动备份",
                10f, Ui.MUTED, false);
        hint.setPadding(Ui.dp(this, 18), Ui.dp(this, 6),
                Ui.dp(this, 18), Ui.dp(this, 2));
        hint.setMaxLines(2);
        return hint;
    }

    private View buildProgress() {
        progressPanel = new LinearLayout(this);
        ((LinearLayout) progressPanel).setOrientation(LinearLayout.VERTICAL);
        progressPanel.setPadding(Ui.dp(this, 16), Ui.dp(this, 10),
                Ui.dp(this, 16), Ui.dp(this, 10));
        progressPanel.setBackground(Ui.round(Ui.CARD, 12f, this));
        LinearLayout.LayoutParams panelParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        panelParams.setMargins(Ui.dp(this, 18), Ui.dp(this, 6),
                Ui.dp(this, 18), 0);
        progressPanel.setLayoutParams(panelParams);
        progressBar = new ProgressBar(this, null,
                android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setProgressTintList(
                android.content.res.ColorStateList.valueOf(Ui.ACCENT));
        progressBar.setProgressBackgroundTintList(
                android.content.res.ColorStateList.valueOf(0xFF252C42));
        ((LinearLayout) progressPanel).addView(progressBar,
                new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 5)));
        progressText = Ui.text(this, "", 10.5f, Ui.MUTED, false);
        progressText.setPadding(0, Ui.dp(this, 6), 0, 0);
        ((LinearLayout) progressPanel).addView(progressText);
        progressPanel.setVisibility(View.GONE);
        return progressPanel;
    }

    private View buildBottomBar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(Ui.dp(this, 18), Ui.dp(this, 9),
                Ui.dp(this, 18), Ui.dp(this, 9));
        bar.setBackground(Ui.bordered(Ui.CARD, 0f, Ui.BORDER, this));

        LinearLayout info = new LinearLayout(this);
        info.setOrientation(LinearLayout.VERTICAL);
        bar.addView(info, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        LinearLayout line = new LinearLayout(this);
        line.setOrientation(LinearLayout.HORIZONTAL);
        line.setGravity(Gravity.CENTER_VERTICAL);
        info.addView(line);
        selectionInfo = Ui.text(this, "已选 0 个 · 0 B", 12.5f, Ui.TEXT, true);
        line.addView(selectionInfo);
        clearButton = Ui.text(this, "清空", 10.5f, Ui.ACCENT, false);
        clearButton.setPadding(Ui.dp(this, 9), Ui.dp(this, 2),
                Ui.dp(this, 2), Ui.dp(this, 2));
        clearButton.setClickable(true);
        clearButton.setOnClickListener(v -> clearSelection());
        line.addView(clearButton);
        selectionHint = Ui.text(this, "原片自动备份，可随时恢复",
                9.5f, Ui.MUTED, false);
        selectionHint.setPadding(0, Ui.dp(this, 3), 0, 0);
        info.addView(selectionHint);

        previewButton = Ui.button(this, "预览", false);
        previewButton.setTextSize(11f);
        previewButton.setPadding(Ui.dp(this, 10), Ui.dp(this, 8),
                Ui.dp(this, 10), Ui.dp(this, 8));
        previewButton.setOnClickListener(v -> previewSelection());
        bar.addView(previewButton);

        compressButton = Ui.button(this, "压缩", true);
        compressButton.setTextSize(12.5f);
        compressButton.setPadding(Ui.dp(this, 16), Ui.dp(this, 10),
                Ui.dp(this, 16), Ui.dp(this, 10));
        compressButton.setOnClickListener(v -> {
            if (busy) {
                if (applying) return;
                cancelRequested.set(true);
                compressButton.setText("正在停止…");
            } else {
                startQualityDialog();
            }
        });
        LinearLayout.LayoutParams compressParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        compressParams.setMarginStart(Ui.dp(this, 6));
        bar.addView(compressButton, compressParams);
        return bar;
    }

    private void openBackups() {
        if (busy || scanning) return;
        startActivityForResult(new Intent(this, BackupActivity.class), REQUEST_BACKUP);
    }

    private void selectTab(int next) {
        if (busy || scanning || tab == next) return;
        tab = next;
        selected.clear();
        applyFilters();
    }

    private void setStatusFilter(String value) {
        if (busy || scanning) return;
        statusFilter = value;
        selected.clear();
        applyFilters();
    }

    private void applyFilters() {
        List<MediaItem> base = tab == 0 ? liveItems : videoItems;
        List<MediaItem> rows = new ArrayList<>();
        for (MediaItem item : base) {
            if ("uncompressed".equals(statusFilter) && item.compressed) continue;
            if ("compressed".equals(statusFilter) && !item.compressed) continue;
            if (rangeStart > 0 || rangeEnd > 0) {
                long day = captureDay(item);
                if (rangeStart > 0 && day < rangeStart) continue;
                if (rangeEnd > 0 && day > rangeEnd) continue;
            }
            rows.add(item);
        }
        if (sortBySize) rows.sort((a, b) -> Long.compare(b.size, a.size));
        else rows.sort((a, b) -> Long.compare(captureDay(b), captureDay(a)));
        displayedItems.clear();
        displayedItems.addAll(rows);
        selected.clear();
        if (adapter != null) adapter.setItems(displayedItems);
        updateTabs();
        updateFilterStyles();
        updateSelection();
        showEmptyState();
    }

    private void updateFilterStyles() {
        if (statusAll == null) return;
        styleChip(statusAll, "all".equals(statusFilter));
        styleChip(statusUncompressed, "uncompressed".equals(statusFilter));
        styleChip(statusCompressed, "compressed".equals(statusFilter));
        styleChip(dateButton, rangeStart > 0 || rangeEnd > 0);
        styleChip(sortButton, sortBySize);
        if (rangeStart > 0 && rangeEnd > 0) {
            dateButton.setText(rangeLabel(rangeStart) + "~" + rangeLabel(rangeEnd));
        } else if (rangeStart > 0) {
            dateButton.setText(rangeLabel(rangeStart) + "~");
        } else {
            dateButton.setText("日期");
        }
        sortButton.setText(sortBySize ? "大小" : "时间");
        updateHint();
    }

    private String rangeLabel(long day) {
        return new SimpleDateFormat("MM-dd", Locale.CHINA).format(new Date(day));
    }

    private void updateHint() {
        if (hint == null) return;
        if (rangeStart > 0 || rangeEnd > 0 || !"all".equals(statusFilter)) {
            long bytes = 0;
            for (MediaItem item : displayedItems) bytes += item.size;
            hint.setText(String.format(Locale.CHINA,
                    "当前筛选 %d 个 · %s。压缩结果先预览，确认后才替换原片。",
                    displayedItems.size(), Ui.bytes(bytes)));
        } else {
            hint.setText("压缩结果先预览，确认后才替换原片；原片会自动备份");
        }
    }

    private void updateTabs() {
        if (tabLive == null) return;
        tabLive.setText(String.format(Locale.CHINA, "实况照片 %d", liveItems.size()));
        tabVideo.setText(String.format(Locale.CHINA, "普通视频 %d", videoItems.size()));
        tabLive.setBackground(tab == 0
                ? Ui.bordered(0xFF242A48, 10f, Ui.ACCENT, this)
                : Ui.bordered(Ui.CARD, 10f, Ui.BORDER, this));
        tabLive.setTextColor(tab == 0 ? Ui.TEXT : Ui.MUTED);
        tabVideo.setBackground(tab == 1
                ? Ui.bordered(0xFF242A48, 10f, Ui.ACCENT, this)
                : Ui.bordered(Ui.CARD, 10f, Ui.BORDER, this));
        tabVideo.setTextColor(tab == 1 ? Ui.TEXT : Ui.MUTED);
        if (liveValue != null) liveValue.setText(String.valueOf(liveItems.size()));
        if (videoValue != null) videoValue.setText(String.valueOf(videoItems.size()));
        if (compressedValue != null) {
            int count = 0;
            for (MediaItem item : liveItems) if (item.compressed) count++;
            for (MediaItem item : videoItems) if (item.compressed) count++;
            compressedValue.setText(String.valueOf(count));
        }
    }

    private void showEmptyState() {
        if (emptyView == null) return;
        int total = tab == 0 ? liveItems.size() : videoItems.size();
        if (total == 0) {
            emptyView.setText(scanResult == null
                    ? "正在扫描媒体库…"
                    : "没有找到媒体文件。请确认权限或相册内容。");
        } else if (displayedItems.isEmpty()) {
            emptyView.setText("当前筛选没有文件。");
        } else {
            emptyView.setText("没有可显示的文件。");
        }
    }

    private void toggleItem(MediaItem item) {
        if (selected.contains(item.path)) selected.remove(item.path);
        else selected.add(item.path);
        adapter.refresh();
        updateSelection();
    }

    private void toggleSelectAll() {
        if (busy || scanning) return;
        List<MediaItem> selectable = new ArrayList<>();
        for (MediaItem item : displayedItems) {
            if (!item.compressed) selectable.add(item);
        }
        if (selectable.isEmpty()) {
            toast("当前筛选没有可压缩的文件");
            return;
        }
        boolean all = true;
        for (MediaItem item : selectable) {
            if (!selected.contains(item.path)) {
                all = false;
                break;
            }
        }
        for (MediaItem item : selectable) {
            if (all) selected.remove(item.path);
            else selected.add(item.path);
        }
        adapter.refresh();
        updateSelection();
    }

    private void clearSelection() {
        if (selected.isEmpty() || busy) return;
        int count = selected.size();
        selected.clear();
        adapter.refresh();
        updateSelection();
        toast("已清空 " + count + " 个选择");
    }

    private void updateSelection() {
        if (selectionInfo == null) return;
        long bytes = 0;
        int compressible = 0;
        for (MediaItem item : displayedItems) {
            if (selected.contains(item.path)) {
                bytes += item.size;
                if (!item.compressed) compressible++;
            }
        }
        selectionInfo.setText(String.format(Locale.CHINA,
                "已选 %d 个 · %s", selected.size(), Ui.bytes(bytes)));
        clearButton.setVisibility(selected.isEmpty() ? View.INVISIBLE : View.VISIBLE);
        clearButton.setAlpha(busy ? 0.4f : 1f);
        previewButton.setVisibility(selected.size() == 1 ? View.VISIBLE : View.INVISIBLE);
        previewButton.setAlpha(busy ? 0.4f : 1f);
        if (selectionHint != null) {
            if (busy) {
                selectionHint.setText("处理时请保持 Klean 在前台");
            } else if (selected.isEmpty()) {
                selectionHint.setText("原片自动备份，可随时恢复");
            } else if (compressible == 0) {
                selectionHint.setText("已压缩的照片不会重复处理");
            } else {
                selectionHint.setText("应用前会再次确认并自动备份");
            }
        }
        List<MediaItem> selectable = new ArrayList<>();
        for (MediaItem item : displayedItems) if (!item.compressed) selectable.add(item);
        boolean allSelected = !selectable.isEmpty();
        for (MediaItem item : selectable) {
            if (!selected.contains(item.path)) {
                allSelected = false;
                break;
            }
        }
        selectAllButton.setText(allSelected ? "取消全选" : "全选");
        selectAllButton.setAlpha(busy || scanning || selectable.isEmpty() ? 0.4f : 1f);
        selectAllButton.setClickable(!busy && !scanning && !selectable.isEmpty());
        if (compressButton != null) {
            if (busy) {
                compressButton.setText(applying ? "正在应用…" : "停止压缩");
                compressButton.setEnabled(!applying);
                compressButton.setAlpha(applying ? 0.6f : 1f);
            } else {
                compressButton.setText("压缩");
                boolean enabled = !scanning && compressible > 0;
                compressButton.setEnabled(enabled);
                compressButton.setAlpha(enabled ? 1f : 0.45f);
            }
        }
        if (scanButton != null) {
            scanButton.setAlpha(scanning || busy ? 0.45f : 1f);
            scanButton.setClickable(!scanning && !busy);
        }
        if (backupButton != null) {
            backupButton.setAlpha(scanning || busy ? 0.45f : 1f);
            backupButton.setClickable(!scanning && !busy);
        }
        if (tabLive != null) {
            tabLive.setAlpha(scanning || busy ? 0.5f : 1f);
            tabVideo.setAlpha(scanning || busy ? 0.5f : 1f);
        }
        updateTabs();
    }

    private long captureDay(MediaItem item) {
        Matcher matcher = NAME_DATE.matcher(item.name);
        if (matcher.find()) {
            Calendar calendar = Calendar.getInstance();
            calendar.set(Integer.parseInt(matcher.group(1)),
                    Integer.parseInt(matcher.group(2)) - 1,
                    Integer.parseInt(matcher.group(3)), 0, 0, 0);
            calendar.set(Calendar.MILLISECOND, 0);
            return calendar.getTimeInMillis();
        }
        Calendar calendar = Calendar.getInstance();
        calendar.setTimeInMillis(item.dateMs);
        calendar.set(Calendar.HOUR_OF_DAY, 0);
        calendar.set(Calendar.MINUTE, 0);
        calendar.set(Calendar.SECOND, 0);
        calendar.set(Calendar.MILLISECOND, 0);
        return calendar.getTimeInMillis();
    }

    private void chooseDateFilter() {
        if (busy || scanning) return;
        if (rangeStart > 0 || rangeEnd > 0) {
            new AlertDialog.Builder(this)
                    .setTitle("日期筛选")
                    .setItems(new String[]{"重新选择日期", "清除日期筛选"},
                            (dialog, which) -> {
                                if (which == 0) pickDateRange();
                                else {
                                    rangeStart = 0;
                                    rangeEnd = 0;
                                    selected.clear();
                                    applyFilters();
                                }
                            })
                    .setNegativeButton("取消", null)
                    .show();
        } else {
            pickDateRange();
        }
    }

    private void pickDateRange() {
        Calendar calendar = Calendar.getInstance();
        if (rangeStart > 0) calendar.setTimeInMillis(rangeStart);
        DatePickerDialog startPicker = new DatePickerDialog(this,
                (view, year, month, dayOfMonth) -> {
                    long start = dayMillis(year, month, dayOfMonth);
                    Calendar endCal = Calendar.getInstance();
                    endCal.setTimeInMillis(Math.max(start,
                            rangeEnd > 0 ? rangeEnd : start));
                    DatePickerDialog endPicker = new DatePickerDialog(this,
                            (endView, endYear, endMonth, endDay) -> {
                                long end = dayMillis(endYear, endMonth, endDay);
                                rangeStart = Math.min(start, end);
                                rangeEnd = Math.max(start, end);
                                selected.clear();
                                applyFilters();
                            },
                            endCal.get(Calendar.YEAR), endCal.get(Calendar.MONTH),
                            endCal.get(Calendar.DAY_OF_MONTH));
                    endPicker.setTitle("选择结束日期");
                    endPicker.show();
                },
                calendar.get(Calendar.YEAR), calendar.get(Calendar.MONTH),
                calendar.get(Calendar.DAY_OF_MONTH));
        startPicker.setTitle("选择开始日期");
        startPicker.show();
    }

    private long dayMillis(int year, int month, int day) {
        Calendar calendar = Calendar.getInstance();
        calendar.set(year, month, day, 0, 0, 0);
        calendar.set(Calendar.MILLISECOND, 0);
        return calendar.getTimeInMillis();
    }

    private void previewSelection() {
        if (selected.size() != 1) return;
        String path = selected.iterator().next();
        for (MediaItem item : displayedItems) {
            if (item.path.equals(path)) {
                previewFile(new File(item.path), item.name);
                return;
            }
        }
    }

    private void previewFile(File file, String title) {
        try {
            LinearLayout content = new LinearLayout(this);
            content.setOrientation(LinearLayout.VERTICAL);
            content.setPadding(Ui.dp(this, 12), Ui.dp(this, 8),
                    Ui.dp(this, 12), Ui.dp(this, 12));
            android.widget.VideoView video = new android.widget.VideoView(this);
            android.widget.MediaController controller =
                    new android.widget.MediaController(this);
            controller.setAnchorView(video);
            video.setMediaController(controller);
            video.setVideoPath(file.getAbsolutePath());
            content.addView(video, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 260)));
            AlertDialog dialog = new AlertDialog.Builder(this)
                    .setTitle(title)
                    .setView(content)
                    .setPositiveButton("关闭", null)
                    .create();
            dialog.setOnDismissListener(d -> video.stopPlayback());
            dialog.show();
            video.start();
        } catch (Exception error) {
            toast("预览失败：" + error.getMessage());
        }
    }

    private void startQualityDialog() {
        List<MediaItem> chosen = new ArrayList<>();
        int compressed = 0;
        for (MediaItem item : displayedItems) {
            if (!selected.contains(item.path)) continue;
            if (item.compressed) compressed++;
            else chosen.add(item);
        }
        if (chosen.isEmpty()) {
            toast(compressed > 0 ? "已压缩的文件不会重复处理" : "先选择文件");
            return;
        }
        if (compressed > 0) toast("将跳过 " + compressed + " 个已压缩文件");
        new AlertDialog.Builder(this)
                .setTitle("压缩质量")
                .setSingleChoiceItems(QUALITY_LABELS, qualityIndex,
                        (dialog, which) -> qualityIndex = which)
                .setNegativeButton("取消", null)
                .setPositiveButton("开始压缩", (dialog, which) -> {
                    getSharedPreferences(SETTINGS, MODE_PRIVATE).edit()
                            .putInt("quality", qualityIndex).apply();
                    startCompression();
                })
                .show();
    }

    private void startCompression() {
        if (busy || scanning) return;
        List<MediaItem> chosen = new ArrayList<>();
        for (MediaItem item : displayedItems) {
            if (selected.contains(item.path) && !item.compressed) chosen.add(item);
        }
        if (chosen.isEmpty()) {
            toast("先选择未压缩的文件");
            return;
        }
        long sourceBytes = 0;
        for (MediaItem item : chosen) sourceBytes += item.size;
        long needed = Math.round(sourceBytes * 0.7d) + 100L * 1024 * 1024;
        if (freeBytes() < needed) {
            new AlertDialog.Builder(this)
                    .setTitle("空间不足")
                    .setMessage("本批约需 " + Ui.bytes(needed) + "，当前可用 "
                            + Ui.bytes(freeBytes()) + "。建议先选少一些。")
                    .setPositiveButton("知道了", null)
                    .show();
            return;
        }

        Storage.clearOldJobs(this);
        File jobDir = Storage.newJob(this);
        Job job = new Job(jobDir, qualityIndex);
        for (MediaItem item : chosen) job.items.add(new JobItem(item));
        currentJob = job;
        busy = true;
        applying = false;
        cancelRequested.set(false);
        outcomes.clear();
        selected.clear();
        adapter.refresh();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        showProgress(true, false, "准备压缩…");
        progressBar.setMax(100);
        progressBar.setProgress(0);
        updateSelection();
        final long startedAt = SystemClock.elapsedRealtime();
        io.execute(() -> {
            int index = 0;
            for (JobItem item : job.items) {
                if (cancelRequested.get()) {
                    item.status = "skipped";
                    item.reason = "已停止";
                    continue;
                }
                final int current = index;
                final int total = job.items.size();
                main.post(() -> progressText.setText(String.format(Locale.CHINA,
                        "%d/%d · %s", current + 1, total, item.item.name)));
                File output = new File(job.dir, uniqueName(job, item.item));
                try {
                    Transcoder.Result result = Transcoder.transcode(
                            item.item, output, QUALITY_FACTORS[job.qualityIndex],
                            new Transcoder.Callback() {
                                @Override
                                public void onProgress(float fraction, String phase) {
                                    main.post(() -> {
                                        progressBar.setProgress((int) Math.round(
                                                (current + fraction) * 100 / total));
                                        progressText.setText(String.format(Locale.CHINA,
                                                "%d/%d · %s · %s",
                                                current + 1, total, item.item.name, phase));
                                    });
                                }

                                @Override
                                public boolean isCancelled() {
                                    return cancelRequested.get();
                                }
                            });
                    item.result = result;
                    item.output = output;
                    if (result.profitable) {
                        item.status = "ready";
                        outcomes.put(item.item.path,
                                TrialOutcome.success(result, output.getAbsolutePath()));
                    } else {
                        item.status = "skipped";
                        item.reason = "压缩后没有变小";
                        output.delete();
                        outcomes.put(item.item.path,
                                TrialOutcome.success(result, output.getAbsolutePath()));
                    }
                } catch (Transcoder.CancelledException cancelled) {
                    output.delete();
                    item.status = "skipped";
                    item.reason = "已停止";
                } catch (Exception error) {
                    output.delete();
                    item.status = "error";
                    item.reason = error.getMessage() == null
                            ? error.getClass().getSimpleName() : error.getMessage();
                    outcomes.put(item.item.path, TrialOutcome.failed(item.reason));
                    Log.w(TAG, "compress failed " + item.item.name, error);
                }
                index++;
                final int done = index;
                main.post(() -> {
                    progressBar.setProgress((int) Math.round(done * 100.0 / total));
                    adapter.refresh();
                    updateSelection();
                });
            }
            job.elapsedMs = SystemClock.elapsedRealtime() - startedAt;
            main.post(() -> finishCompression(job));
        });
    }

    private String uniqueName(Job job, MediaItem item) {
        String base = item.stem().replaceAll("[\\\\/:*?\"<>|]", "_");
        String name = base + ".klean.mp4";
        int counter = 2;
        while (new File(job.dir, name).exists()) {
            name = base + "-" + counter + ".klean.mp4";
            counter++;
        }
        return name;
    }

    private void finishCompression(Job job) {
        busy = false;
        cancelRequested.set(false);
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        showProgress(false, false, "");
        progressBar.setProgress(0);
        adapter.refresh();
        updateSelection();
        int ready = 0;
        for (JobItem item : job.items) if ("ready".equals(item.status)) ready++;
        if (ready == 0) {
            Storage.deleteTree(job.dir);
            currentJob = null;
            outcomes.clear();
            adapter.refresh();
            updateSelection();
            new AlertDialog.Builder(this)
                    .setTitle("没有可应用的结果")
                    .setMessage("压缩后没有得到更小的文件，原片未修改。")
                    .setPositiveButton("知道了", null)
                    .show();
            return;
        }
        currentJob = job;
        showResultDialog(job);
    }

    private void showResultDialog(Job job) {
        applySelection.clear();
        for (JobItem item : job.items) {
            if ("ready".equals(item.status)) applySelection.add(item.item.path);
        }
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(Ui.dp(this, 18), Ui.dp(this, 12),
                Ui.dp(this, 18), Ui.dp(this, 12));

        int ready = 0, skipped = 0, errors = 0;
        long source = 0, output = 0, duration = 0;
        for (JobItem item : job.items) {
            if ("ready".equals(item.status)) {
                ready++;
                source += item.result.sourceBytes;
                output += item.result.outputBytes;
                duration += item.result.contentDurationMs;
            } else if ("skipped".equals(item.status)) skipped++;
            else errors++;
        }
        double factor = job.elapsedMs > 0 && duration > 0
                ? (duration / 1000.0) / (job.elapsedMs / 1000.0) : 0;
        int saved = source > 0
                ? (int) Math.round((source - output) * 100.0 / source) : 0;

        content.addView(Ui.text(this, String.format(Locale.CHINA,
                "成功 %d · 跳过 %d · 失败 %d", ready, skipped, errors),
                13f, Ui.TEXT, true));
        TextView summary = Ui.text(this, String.format(Locale.CHINA,
                "本批约省 %d%% · 平均 %.2f× 实时 · 处理 %.1f 秒\n"
                        + "勾选要应用的结果，预览确认后再替换原片。",
                saved, factor, job.elapsedMs / 1000.0), 10.5f, Ui.MUTED, false);
        summary.setPadding(0, Ui.dp(this, 6), 0, Ui.dp(this, 8));
        summary.setLineSpacing(Ui.dp(this, 2), 1f);
        content.addView(summary);

        ScrollView scroll = new ScrollView(this);
        LinearLayout rows = new LinearLayout(this);
        rows.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(rows);
        content.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 360)));

        for (JobItem item : job.items) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(0, Ui.dp(this, 6), 0, Ui.dp(this, 6));
            if ("ready".equals(item.status)) {
                CheckBox check = new CheckBox(this);
                check.setChecked(true);
                check.setButtonTintList(new android.content.res.ColorStateList(
                        new int[][]{
                                new int[]{android.R.attr.state_checked},
                                new int[]{}
                        },
                        new int[]{Ui.ACCENT, 0xFF59627C}));
                row.addView(check, new LinearLayout.LayoutParams(
                        Ui.dp(this, 32), Ui.dp(this, 40)));
                check.setOnCheckedChangeListener((buttonView, checked) -> {
                    if (checked) applySelection.add(item.item.path);
                    else applySelection.remove(item.item.path);
                    updateResultApplyLabel();
                });
                LinearLayout texts = new LinearLayout(this);
                texts.setOrientation(LinearLayout.VERTICAL);
                texts.addView(Ui.text(this, item.item.name, 11.5f, Ui.TEXT, true));
                int percent = item.result.sourceBytes > 0
                        ? (int) Math.round((item.result.sourceBytes
                        - item.result.outputBytes) * 100.0 / item.result.sourceBytes) : 0;
                texts.addView(Ui.text(this, String.format(Locale.CHINA,
                        "%s → %s · 省 %d%% · %.1f×",
                        Ui.bytes(item.result.sourceBytes),
                        Ui.bytes(item.result.outputBytes), percent,
                        item.result.contentDurationMs
                                / (double) Math.max(1, item.result.elapsedMs)),
                        9.5f, Ui.MUTED, false));
                LinearLayout.LayoutParams textParams = new LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                textParams.setMarginStart(Ui.dp(this, 4));
                row.addView(texts, textParams);
                TextView preview = Ui.text(this, "预览", 10.5f, Ui.ACCENT, true);
                preview.setPadding(Ui.dp(this, 8), Ui.dp(this, 4),
                        Ui.dp(this, 8), Ui.dp(this, 4));
                preview.setClickable(true);
                preview.setOnClickListener(v -> previewFile(item.output, item.item.name));
                row.addView(preview);
            } else {
                LinearLayout texts = new LinearLayout(this);
                texts.setOrientation(LinearLayout.VERTICAL);
                String label = "skipped".equals(item.status) ? "跳过" : "失败";
                texts.addView(Ui.text(this, item.item.name + " · " + label,
                        11f, "skipped".equals(item.status) ? Ui.WARN : Ui.DANGER, true));
                texts.addView(Ui.text(this, item.reason == null ? "" : item.reason,
                        9.5f, Ui.MUTED, false));
                row.addView(texts, new LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            }
            rows.addView(row);
        }

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setGravity(Gravity.END);
        actions.setPadding(0, Ui.dp(this, 10), 0, 0);
        TextView discard = Ui.button(this, "放弃结果", false);
        discard.setPadding(Ui.dp(this, 14), Ui.dp(this, 9),
                Ui.dp(this, 14), Ui.dp(this, 9));
        actions.addView(discard, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView apply = Ui.button(this, "应用到手机", true);
        apply.setPadding(Ui.dp(this, 14), Ui.dp(this, 9),
                Ui.dp(this, 14), Ui.dp(this, 9));
        LinearLayout.LayoutParams applyParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        applyParams.setMarginStart(Ui.dp(this, 8));
        actions.addView(apply, applyParams);
        content.addView(actions);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setView(content)
                .create();
        dialog.setCancelable(false);
        resultApplyButton = apply;
        discard.setOnClickListener(v -> confirmDiscard(job, dialog));
        apply.setOnClickListener(v -> confirmApply(job, dialog));
        dialog.show();
        if (dialog.getWindow() != null) {
            dialog.getWindow().setLayout(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
        }
        updateResultApplyLabel();
    }

    private void updateResultApplyLabel() {
        if (resultApplyButton == null) return;
        resultApplyButton.setText("应用到手机（" + applySelection.size() + "）");
        resultApplyButton.setEnabled(!applySelection.isEmpty());
        resultApplyButton.setAlpha(applySelection.isEmpty() ? 0.45f : 1f);
    }

    private void confirmDiscard(Job job, AlertDialog dialog) {
        new AlertDialog.Builder(this)
                .setTitle("放弃这批结果？")
                .setMessage("将删除本地压缩结果，手机原片不受影响。")
                .setNegativeButton("继续预览", null)
                .setPositiveButton("放弃", (d, w) -> {
                    Storage.deleteTree(job.dir);
                    currentJob = null;
                    outcomes.clear();
                    adapter.refresh();
                    updateSelection();
                    dialog.dismiss();
                })
                .show();
    }

    private void confirmApply(Job job, AlertDialog dialog) {
        List<JobItem> chosen = new ArrayList<>();
        long bytes = 0;
        for (JobItem item : job.items) {
            if ("ready".equals(item.status)
                    && applySelection.contains(item.item.path)) {
                chosen.add(item);
                bytes += item.result.sourceBytes;
            }
        }
        if (chosen.isEmpty()) return;
        long needed = bytes + 80L * 1024 * 1024;
        if (freeBytes() < needed) {
            toast("空间不足：备份这些原片约需 " + Ui.bytes(needed));
            return;
        }
        String message = String.format(Locale.CHINA,
                "将应用 %d 个压缩结果。\n\n"
                        + "• 原片会先备份到 App 的「备份」页（约 %s）\n"
                        + "• 只替换 MP4，JPG 定格画面不改\n"
                        + "• 实况 MP4 会保留 vivo 标识\n"
                        + "• 应用后可在「备份」里恢复\n"
                        + "• 备份在 App 私有目录；卸载 App 会一起删除\n\n"
                        + "备份和替换需要约 %s 空间，当前可用 %s。",
                chosen.size(), Ui.bytes(bytes), Ui.bytes(needed), Ui.bytes(freeBytes()));
        new AlertDialog.Builder(this)
                .setTitle("确认应用到手机")
                .setMessage(message)
                .setNegativeButton("取消", null)
                .setPositiveButton("开始应用", (d, w) -> {
                    dialog.dismiss();
                    runApply(job, chosen);
                })
                .show();
    }

    private void runApply(Job job, List<JobItem> chosen) {
        busy = true;
        applying = true;
        cancelRequested.set(false);
        outcomes.clear();
        selected.clear();
        adapter.refresh();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        showProgress(true, false, "准备备份并应用…");
        progressBar.setMax(100);
        progressBar.setProgress(0);
        updateSelection();
        File batchDir = Storage.newBatch(this);
        List<Storage.BackupRecord> records = new ArrayList<>();
        io.execute(() -> {
            int applied = 0;
            int failed = 0;
            int index = 0;
            for (JobItem item : chosen) {
                final int current = index;
                final int total = chosen.size();
                main.post(() -> progressText.setText(String.format(Locale.CHINA,
                        "%d/%d · 备份并应用 %s", current + 1, total, item.item.name)));
                try {
                    Storage.BackupRecord record = Storage.apply(
                            MainActivity.this,
                            item.item,
                            item.output,
                            item.result.sourceMd5,
                            item.result.outputMd5,
                            item.result.outputBytes,
                            batchDir,
                            QUALITY_LABELS[job.qualityIndex]);
                    records.add(record);
                    Storage.writeManifest(batchDir, records);
                    Storage.HistoryEntry entry = new Storage.HistoryEntry();
                    entry.originalSize = record.originalSize;
                    entry.compressedSize = record.compressedSize;
                    entry.originalMd5 = record.originalMd5;
                    entry.compressedMd5 = record.compressedMd5;
                    entry.preset = record.preset;
                    entry.batch = batchDir.getName();
                    entry.backup = record.backup;
                    entry.mtime = record.mtime;
                    Storage.putHistory(MainActivity.this, record.path, entry);
                    history.put(record.path, entry);
                    item.item.compressed = true;
                    item.item.size = record.compressedSize;
                    item.status = "applied";
                    if (item.output != null) item.output.delete();
                    applied++;
                } catch (Exception error) {
                    item.status = "error";
                    item.reason = error.getMessage() == null
                            ? error.getClass().getSimpleName() : error.getMessage();
                    failed++;
                    Log.w(TAG, "apply failed " + item.item.name, error);
                }
                index++;
                final int done = index;
                main.post(() -> {
                    progressBar.setProgress((int) Math.round(done * 100.0 / total));
                    updateSelection();
                    adapter.refresh();
                });
            }
            if (records.isEmpty()) Storage.deleteTree(batchDir);
            Storage.deleteTree(job.dir);
            final int appliedCount = applied;
            final int failedCount = failed;
            final int backupCount = records.size();
            main.post(() -> finishApply(appliedCount, failedCount, backupCount, batchDir));
        });
    }

    private void finishApply(int applied, int failed, int backupCount, File batchDir) {
        busy = false;
        applying = false;
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        showProgress(false, false, "");
        progressBar.setProgress(0);
        currentJob = null;
        outcomes.clear();
        adapter.refresh();
        updateTabs();
        updateSelection();
        applyFilters();
        String message = String.format(Locale.CHINA,
                "成功应用 %d 个，失败 %d 个。\n\n"
                        + "原片备份：%d 个（%s）\n"
                        + "只替换了 MP4，JPG 没有改动。\n"
                        + "可在顶部「备份」里预览、恢复或删除备份。",
                applied, failed, backupCount, batchDir.getAbsolutePath());
        new AlertDialog.Builder(this)
                .setTitle(applied > 0 ? "已应用到手机" : "应用失败")
                .setMessage(message)
                .setNegativeButton("完成", null)
                .setPositiveButton("查看备份", (d, w) -> openBackups())
                .show();
    }

    private void startScan() {
        if (!mainBuilt || scanning || busy) return;
        scanning = true;
        scanResult = null;
        selected.clear();
        outcomes.clear();
        updateSelection();
        showProgress(true, true, "正在扫描媒体库…");
        io.execute(() -> {
            try {
                MediaScanner.Result result = MediaScanner.scan(this, message ->
                        main.post(() -> progressText.setText(message)));
                main.post(() -> applyScan(result));
            } catch (Exception error) {
                Log.e(TAG, "scan failed", error);
                main.post(() -> {
                    scanning = false;
                    showProgress(false, false, "");
                    updateSelection();
                    hint.setText("扫描失败：" + error.getMessage()
                            + "\n请确认已授予「所有文件访问」权限。");
                    emptyView.setText("扫描失败，请检查存储权限后重试。");
                });
            }
        });
    }

    private void applyScan(MediaScanner.Result result) {
        if (isFinishing() || isDestroyed()) return;
        scanResult = result;
        liveItems.clear();
        liveItems.addAll(result.live);
        videoItems.clear();
        videoItems.addAll(result.videos);
        history.clear();
        history.putAll(Storage.loadHistory(this));
        for (MediaItem item : liveItems) markCompressed(item);
        for (MediaItem item : videoItems) markCompressed(item);
        scanning = false;
        showProgress(false, false, "");
        applyFilters();
        showEmptyState();
        Log.d(TAG, "scan done live=" + liveItems.size() + " videos=" + videoItems.size());
    }

    private void markCompressed(MediaItem item) {
        Storage.HistoryEntry entry = history.get(item.path);
        item.compressed = entry != null && entry.compressedSize == item.size;
    }

    private void showProgress(boolean visible, boolean indeterminate, String text) {
        if (progressPanel == null) return;
        progressPanel.setVisibility(visible ? View.VISIBLE : View.GONE);
        progressBar.setIndeterminate(indeterminate);
        if (!indeterminate) {
            progressBar.setMax(100);
            progressBar.setProgress(0);
        }
        progressText.setText(text);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show();
    }

    private long freeBytes() {
        try {
            StatFs stat = new StatFs(
                    Environment.getExternalStorageDirectory().getAbsolutePath());
            return stat.getAvailableBytes();
        } catch (Exception error) {
            return 0;
        }
    }

    private static final class Job {
        final File dir;
        final int qualityIndex;
        final List<JobItem> items = new ArrayList<>();
        long elapsedMs;

        Job(File dir, int qualityIndex) {
            this.dir = dir;
            this.qualityIndex = qualityIndex;
        }
    }

    private static final class JobItem {
        final MediaItem item;
        String status = "pending";
        String reason = "";
        File output;
        Transcoder.Result result;

        JobItem(MediaItem item) {
            this.item = item;
        }
    }
}
