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
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.recyclerview.widget.GridLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

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
import java.util.TreeMap;
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
    private String dateMode = "";
    private String albumFilter = "";
    private final Set<String> expandedMonths = new HashSet<>();
    private FrameLayout drawerOverlay;
    private LinearLayout drawerPanel;
    private LinearLayout drawerContent;
    private String drawerTab = "date";
    private boolean drawerOpen;
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
    private TextView statLabel1;
    private TextView statLabel2;
    private TextView statLabel3;
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
    private RecyclerView groupedList;
    private MonthAdapter groupedAdapter;
    private TextView scanButton;
    private TextView backupButton;
    private ImageView galleryButton;
    private TextView modeLabel;
    private FrameLayout modeOverlay;
    private TextView startLiveCount;
    private TextView startLiveSize;
    private TextView startVideoCount;
    private TextView startVideoSize;
    private TextView modeClose;
    private boolean modeChosen;
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
        page.addView(buildModeBar());
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
        emptyView = empty;
        groupedList = new RecyclerView(this);
        GridLayoutManager layout = new GridLayoutManager(this, 3);
        groupedAdapter = new MonthAdapter(this, thumbnails, selected, outcomes,
                this::updateSelection);
        layout.setSpanSizeLookup(new GridLayoutManager.SpanSizeLookup() {
            @Override
            public int getSpanSize(int position) {
                return groupedAdapter.spanSize(position);
            }
        });
        groupedList.setLayoutManager(layout);
        groupedList.setItemAnimator(null);
        groupedList.setBackgroundColor(Color.TRANSPARENT);
        groupedList.setPadding(Ui.dp(this, 9), Ui.dp(this, 6),
                Ui.dp(this, 9), Ui.dp(this, 12));
        groupedList.setClipToPadding(false);
        groupedList.setVisibility(View.GONE);
        groupedList.setAdapter(groupedAdapter);
        listWrap.addView(groupedList, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        page.addView(buildProgress());
        page.addView(buildBottomBar());
        root.addView(buildDrawerOverlay());
        root.addView(buildModeOverlay());
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
        TextView sub = Ui.text(this, "本机压缩 · 自动备份",
                9.5f, Ui.MUTED, false);
        sub.setPadding(0, Ui.dp(this, 3), 0, 0);
        sub.setSingleLine(true);
        sub.setEllipsize(android.text.TextUtils.TruncateAt.END);
        titleColumn.addView(sub);

        galleryButton = new ImageView(this);
        galleryButton.setImageResource(R.drawable.ic_filter);
        galleryButton.setScaleType(ImageView.ScaleType.FIT_CENTER);
        galleryButton.setPadding(Ui.dp(this, 9), Ui.dp(this, 9),
                Ui.dp(this, 9), Ui.dp(this, 9));
        galleryButton.setBackground(Ui.bordered(0xFF1A2033, 11f, 0xFF3A4268, this));
        galleryButton.setColorFilter(Ui.ACCENT);
        galleryButton.setContentDescription("筛选、按月份或相册浏览");
        galleryButton.setClickable(true);
        galleryButton.setFocusable(true);
        galleryButton.setOnClickListener(v -> openDrawer());
        LinearLayout.LayoutParams galleryParams = new LinearLayout.LayoutParams(
                Ui.dp(this, 40), Ui.dp(this, 40));
        galleryParams.setMarginEnd(Ui.dp(this, 6));
        header.addView(galleryButton, galleryParams);

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
        liveValue = addStat(card, "–", "实况照片", 0);
        addDivider(card);
        videoValue = addStat(card, "–", "总大小", 1);
        addDivider(card);
        compressedValue = addStat(card, "0", "已压缩", 2);
        return card;
    }

    private TextView addStat(LinearLayout parent, String value, String label, int slot) {
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
        if (slot == 0) statLabel1 = labelView;
        else if (slot == 1) statLabel2 = labelView;
        else statLabel3 = labelView;
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

    private View buildModeBar() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rowParams.setMargins(Ui.dp(this, 18), Ui.dp(this, 8),
                Ui.dp(this, 18), 0);
        row.setLayoutParams(rowParams);

        TextView modeSwitch = Ui.button(this, "模式", false);
        modeSwitch.setTextSize(11f);
        modeSwitch.setPadding(Ui.dp(this, 10), Ui.dp(this, 7),
                Ui.dp(this, 10), Ui.dp(this, 7));
        modeSwitch.setOnClickListener(v -> showModeChooser());
        row.addView(modeSwitch);

        modeLabel = Ui.text(this, "实况照片 · 0", 12.5f, Ui.TEXT, true);
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        labelParams.setMarginStart(Ui.dp(this, 9));
        row.addView(modeLabel, labelParams);

        selectAllButton = Ui.button(this, "全选", false);
        selectAllButton.setTextSize(11f);
        selectAllButton.setPadding(Ui.dp(this, 11), Ui.dp(this, 7),
                Ui.dp(this, 11), Ui.dp(this, 7));
        row.addView(selectAllButton);
        selectAllButton.setOnClickListener(v -> toggleSelectAll());
        return row;
    }

    private View buildModeOverlay() {
        modeOverlay = new FrameLayout(this);
        modeOverlay.setBackgroundResource(R.drawable.bg_app);
        modeOverlay.setClickable(true);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(Ui.dp(this, 24), Ui.dp(this, 24),
                Ui.dp(this, 24), Ui.dp(this, 28));
        scroll.addView(page);
        modeOverlay.addView(scroll, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        page.addView(top);
        top.addView(Ui.logo(this, 42));
        LinearLayout brand = new LinearLayout(this);
        brand.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams brandParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        brandParams.setMarginStart(Ui.dp(this, 11));
        top.addView(brand, brandParams);
        brand.addView(Ui.text(this, "Klean", 20f, Ui.TEXT, true));
        brand.addView(Ui.text(this, "Android 本机压缩与备份", 9.5f, Ui.MUTED, false));
        modeClose = Ui.text(this, "×", 24f, Ui.MUTED, true);
        modeClose.setPadding(Ui.dp(this, 10), 0, Ui.dp(this, 4), 0);
        modeClose.setClickable(true);
        modeClose.setOnClickListener(v -> modeOverlay.setVisibility(View.GONE));
        modeClose.setVisibility(modeChosen ? View.VISIBLE : View.GONE);
        top.addView(modeClose);

        TextView title = Ui.text(this, "选择要处理的类型", 25f, Ui.TEXT, true);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        titleParams.topMargin = Ui.dp(this, 28);
        page.addView(title, titleParams);
        TextView sub = Ui.text(this,
                "实况照片和普通视频是两条完全独立的赛道，列表、选择、历史和备份互不影响。",
                11.5f, Ui.MUTED, false);
        sub.setLineSpacing(Ui.dp(this, 3), 1f);
        LinearLayout.LayoutParams subParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        subParams.topMargin = Ui.dp(this, 7);
        subParams.bottomMargin = Ui.dp(this, 10);
        page.addView(sub, subParams);

        page.addView(buildModeCard(true));
        page.addView(buildModeCard(false));

        TextView note = Ui.text(this,
                "两条赛道的任务不会混在一起；应用前会自动备份原片，JPG 定格画面永不修改。",
                10f, Ui.MUTED, false);
        note.setLineSpacing(Ui.dp(this, 2), 1f);
        LinearLayout.LayoutParams noteParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        noteParams.topMargin = Ui.dp(this, 14);
        page.addView(note, noteParams);
        refreshModeStats();
        return modeOverlay;
    }

    private View buildModeCard(boolean live) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(Ui.dp(this, 18), Ui.dp(this, 16),
                Ui.dp(this, 18), Ui.dp(this, 14));
        card.setBackground(Ui.bordered(Ui.CARD, 16f, Ui.BORDER, this));
        card.setClickable(true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.topMargin = Ui.dp(this, 9);
        card.setLayoutParams(params);

        ImageView icon = new ImageView(this);
        icon.setImageResource(live ? R.drawable.ic_live : R.drawable.ic_video);
        icon.setColorFilter(live ? Ui.ACCENT : Ui.BLUE);
        icon.setScaleType(ImageView.ScaleType.FIT_CENTER);
        icon.setPadding(Ui.dp(this, 10), Ui.dp(this, 10),
                Ui.dp(this, 10), Ui.dp(this, 10));
        icon.setBackground(Ui.round(live ? 0x332F7BFF : 0x228B7CFF, 11f, this));
        card.addView(icon, new LinearLayout.LayoutParams(
                Ui.dp(this, 46), Ui.dp(this, 46)));
        TextView title = Ui.text(this, live ? "实况照片" : "普通视频",
                21f, Ui.TEXT, true);
        title.setPadding(0, Ui.dp(this, 10), 0, 0);
        card.addView(title);
        TextView desc = Ui.text(this, live
                        ? "压缩伴生 MP4，保留 vivo 实况标识；JPG 定格画面永不修改。"
                        : "独立压缩普通 MP4，独立备份，按总大小分批更稳。",
                11f, Ui.MUTED, false);
        desc.setLineSpacing(Ui.dp(this, 2), 1f);
        desc.setPadding(0, Ui.dp(this, 5), 0, Ui.dp(this, 8));
        card.addView(desc);

        LinearLayout stats = new LinearLayout(this);
        stats.setOrientation(LinearLayout.HORIZONTAL);
        card.addView(stats);
        TextView count = Ui.text(this, "—", 18f, Ui.TEXT, true);
        stats.addView(count, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView size = Ui.text(this, "—", 18f, Ui.TEXT, true);
        stats.addView(size, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        LinearLayout labels = new LinearLayout(this);
        labels.setOrientation(LinearLayout.HORIZONTAL);
        card.addView(labels);
        TextView countLabel = Ui.text(this, live ? "实况照片" : "普通视频",
                9f, Ui.MUTED, false);
        labels.addView(countLabel, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        labels.addView(Ui.text(this, live ? "伴生 MP4" : "总大小",
                9f, Ui.MUTED, false), new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView enter = Ui.text(this, live ? "进入实况 →" : "进入视频 →",
                11.5f, live ? Ui.ACCENT : Ui.BLUE, true);
        enter.setPadding(0, Ui.dp(this, 11), 0, 0);
        card.addView(enter);
        if (live) {
            startLiveCount = count;
            startLiveSize = size;
        } else {
            startVideoCount = count;
            startVideoSize = size;
        }
        card.setOnClickListener(v -> enterMode(live ? 0 : 1));
        return card;
    }

    private void refreshModeStats() {
        if (startLiveCount == null) return;
        long liveBytes = 0;
        for (MediaItem item : liveItems) liveBytes += item.size;
        long videoBytes = 0;
        for (MediaItem item : videoItems) videoBytes += item.size;
        if (scanResult == null) {
            startLiveCount.setText("—");
            startLiveSize.setText("—");
            startVideoCount.setText("—");
            startVideoSize.setText("—");
        } else {
            startLiveCount.setText(String.valueOf(liveItems.size()));
            startLiveSize.setText(Ui.bytes(liveBytes));
            startVideoCount.setText(String.valueOf(videoItems.size()));
            startVideoSize.setText(Ui.bytes(videoBytes));
        }
    }

    private void showModeChooser() {
        if (busy || modeOverlay == null) return;
        closeDrawer();
        refreshModeStats();
        modeClose.setVisibility(modeChosen ? View.VISIBLE : View.GONE);
        modeOverlay.setVisibility(View.VISIBLE);
    }

    private void enterMode(int mode) {
        modeChosen = true;
        tab = mode;
        selected.clear();
        outcomes.clear();
        statusFilter = "all";
        albumFilter = "";
        dateMode = "";
        rangeStart = 0;
        rangeEnd = 0;
        sortBySize = false;
        modeOverlay.setVisibility(View.GONE);
        applyFilters();
        updateTabs();
        updateSelection();
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
        dateButton = chip("浏览");
        sortButton = chip("时间");
        addChip(row, statusAll);
        addChip(row, statusUncompressed);
        addChip(row, statusCompressed);
        addChip(row, dateButton);
        addChip(row, sortButton);
        dateButton.setVisibility(View.GONE);
        statusAll.setOnClickListener(v -> setStatusFilter("all"));
        statusUncompressed.setOnClickListener(v -> setStatusFilter("uncompressed"));
        statusCompressed.setOnClickListener(v -> setStatusFilter("compressed"));
        dateButton.setOnClickListener(v -> openDrawer());
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
        Intent intent = new Intent(this, BackupActivity.class);
        intent.putExtra("live", tab == 0);
        startActivityForResult(intent, REQUEST_BACKUP);
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
            if (!albumFilter.isEmpty()) {
                java.io.File parent = new java.io.File(item.path).getParentFile();
                if (parent == null || !albumFilter.equals(parent.getAbsolutePath())) continue;
            } else if (rangeStart > 0 || rangeEnd > 0) {
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
        boolean grouped = "month".equals(dateMode);
        if (adapter != null) {
            if (grouped) {
                adapter.setItems(new ArrayList<>());
                if (groupedAdapter != null) groupedAdapter.setItems(displayedItems);
            } else {
                if (groupedAdapter != null) groupedAdapter.setItems(new ArrayList<>());
                adapter.setItems(displayedItems);
            }
        }
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
        boolean dateActive = !albumFilter.isEmpty() || rangeStart > 0 || rangeEnd > 0;
        styleChip(sortButton, sortBySize);
        dateButton.setVisibility(dateActive ? View.VISIBLE : View.GONE);
        if (dateActive) {
            styleChip(dateButton, true);
            if (!albumFilter.isEmpty()) {
                dateButton.setText(shortAlbum(albumFilter));
            } else if ("day".equals(dateMode)) {
                dateButton.setText(rangeLabel(rangeStart));
            } else if ("month".equals(dateMode)) {
                dateButton.setText(monthLabel(rangeStart));
            } else if (rangeStart > 0 && rangeEnd > 0) {
                dateButton.setText(rangeLabel(rangeStart) + "~" + rangeLabel(rangeEnd));
            } else {
                dateButton.setText(rangeLabel(rangeStart) + "~");
            }
        }
        if (galleryButton != null) {
            galleryButton.setColorFilter(Ui.ACCENT);
            galleryButton.setBackground(dateActive
                    ? Ui.bordered(0xFF2A3152, 11f, Ui.ACCENT, this)
                    : Ui.bordered(0xFF1A2033, 11f, 0xFF3A4268, this));
        }
        sortButton.setText(sortBySize ? "大小" : "时间");
        updateHint();
    }

    private String rangeLabel(long day) {
        return new SimpleDateFormat("MM-dd", Locale.CHINA).format(new Date(day));
    }

    private String monthLabel(long day) {
        return new SimpleDateFormat("yyyy-MM", Locale.CHINA).format(new Date(day));
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
        List<MediaItem> current = tab == 0 ? liveItems : videoItems;
        long bytes = 0;
        int compressed = 0;
        for (MediaItem item : current) {
            bytes += item.size;
            if (item.compressed) compressed++;
        }
        if (modeLabel != null) {
            modeLabel.setText(String.format(Locale.CHINA, "%s · %d",
                    tab == 0 ? "实况照片" : "普通视频", current.size()));
        }
        if (statLabel1 != null) statLabel1.setText(tab == 0 ? "实况照片" : "普通视频");
        if (liveValue != null) liveValue.setText(String.valueOf(current.size()));
        if (statLabel2 != null) statLabel2.setText("总大小");
        if (videoValue != null) videoValue.setText(Ui.bytes(bytes));
        if (statLabel3 != null) statLabel3.setText("已压缩");
        if (compressedValue != null) compressedValue.setText(String.valueOf(compressed));
    }

    private void showEmptyState() {
        if (emptyView == null) return;
        boolean empty = displayedItems.isEmpty();
        boolean grouped = "month".equals(dateMode);
        if (empty) {
            int total = tab == 0 ? liveItems.size() : videoItems.size();
            emptyView.setText(total == 0
                    ? (scanResult == null ? "正在扫描媒体库…"
                    : "没有找到媒体文件。请确认权限或相册内容。")
                    : "当前筛选没有文件。");
            emptyView.setVisibility(View.VISIBLE);
            listView.setVisibility(View.GONE);
            if (groupedList != null) groupedList.setVisibility(View.GONE);
        } else {
            emptyView.setVisibility(View.GONE);
            listView.setVisibility(grouped ? View.GONE : View.VISIBLE);
            if (groupedList != null) {
                groupedList.setVisibility(grouped ? View.VISIBLE : View.GONE);
                if (grouped) groupedAdapter.refresh();
            }
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
        refreshGroupedSelection();
    }

    private void clearSelection() {
        if (selected.isEmpty() || busy) return;
        int count = selected.size();
        selected.clear();
        adapter.refresh();
        updateSelection();
        refreshGroupedSelection();
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
        String selectBase = "全选";
        if (!albumFilter.isEmpty()) selectBase = "全选相册";
        else if ("day".equals(dateMode)) selectBase = "全选本日";
        else if ("month".equals(dateMode)) selectBase = "全选本月";
        else if ("range".equals(dateMode)) selectBase = "全选范围";
        selectAllButton.setText(allSelected ? "取消全选" : selectBase);
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
        return item.captureDay();
    }

    private void openDrawer() {
        if (busy || scanning || drawerOverlay == null) return;
        if (modeOverlay != null && modeOverlay.getVisibility() == View.VISIBLE) return;
        drawerOpen = true;
        drawerOverlay.setVisibility(View.VISIBLE);
        drawerPanel.animate().translationX(0).setDuration(180).start();
        drawerOverlay.setAlpha(0f);
        drawerOverlay.animate().alpha(1f).setDuration(160).start();
        renderDrawer();
    }

    private void closeDrawer() {
        if (drawerOverlay == null || !drawerOpen) return;
        drawerOpen = false;
        drawerPanel.animate().translationX(-drawerPanel.getWidth())
                .setDuration(160)
                .withEndAction(() -> drawerOverlay.setVisibility(View.GONE))
                .start();
        drawerOverlay.animate().alpha(0f).setDuration(140).start();
    }

    private View buildDrawerOverlay() {
        drawerOverlay = new FrameLayout(this);
        drawerOverlay.setVisibility(View.GONE);
        drawerOverlay.setAlpha(0f);

        View scrim = new View(this);
        scrim.setBackgroundColor(0xB3000000);
        scrim.setOnClickListener(v -> closeDrawer());
        drawerOverlay.addView(scrim, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        int width = Math.round(getResources().getDisplayMetrics().widthPixels * 0.86f);
        drawerPanel = new LinearLayout(this);
        drawerPanel.setOrientation(LinearLayout.VERTICAL);
        drawerPanel.setBackgroundColor(Ui.CARD);
        drawerPanel.setClickable(true);
        FrameLayout.LayoutParams panelParams = new FrameLayout.LayoutParams(
                width, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.START);
        drawerOverlay.addView(drawerPanel, panelParams);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(Ui.dp(this, 18), Ui.dp(this, 16),
                Ui.dp(this, 14), Ui.dp(this, 8));
        drawerPanel.addView(header);
        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        header.addView(titles, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        titles.addView(Ui.text(this, "照片与相册", 20f, Ui.TEXT, true));
        TextView drawerSub = Ui.text(this,
                "按年/月浏览；进入月份后按日期分组，可直接全选某天", 9.5f, Ui.MUTED, false);
        drawerSub.setPadding(0, Ui.dp(this, 3), 0, 0);
        titles.addView(drawerSub);
        TextView close = Ui.text(this, "×", 24f, Ui.MUTED, true);
        close.setGravity(Gravity.CENTER);
        close.setPadding(Ui.dp(this, 10), 0, Ui.dp(this, 4), 0);
        close.setClickable(true);
        close.setOnClickListener(v -> closeDrawer());
        header.addView(close);

        LinearLayout tabs = new LinearLayout(this);
        tabs.setOrientation(LinearLayout.HORIZONTAL);
        tabs.setPadding(Ui.dp(this, 18), Ui.dp(this, 2),
                Ui.dp(this, 18), Ui.dp(this, 8));
        drawerPanel.addView(tabs);
        TextView dateTab = chip("按日期");
        dateTab.setOnClickListener(v -> {
            drawerTab = "date";
            renderDrawer();
        });
        TextView albumTab = chip("按相册");
        albumTab.setOnClickListener(v -> {
            drawerTab = "album";
            renderDrawer();
        });
        addChip(tabs, dateTab);
        addChip(tabs, albumTab);
        drawerPanel.setTag(new TextView[]{dateTab, albumTab});

        ScrollView scroll = new ScrollView(this);
        scroll.setVerticalScrollBarEnabled(false);
        drawerContent = new LinearLayout(this);
        drawerContent.setOrientation(LinearLayout.VERTICAL);
        drawerContent.setPadding(Ui.dp(this, 12), 0,
                Ui.dp(this, 12), Ui.dp(this, 12));
        scroll.addView(drawerContent);
        drawerPanel.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout footer = new LinearLayout(this);
        footer.setOrientation(LinearLayout.HORIZONTAL);
        footer.setGravity(Gravity.CENTER_VERTICAL);
        footer.setPadding(Ui.dp(this, 16), Ui.dp(this, 9),
                Ui.dp(this, 16), Ui.dp(this, 9));
        footer.setBackground(Ui.bordered(Ui.CARD, 0f, Ui.BORDER, this));
        drawerPanel.addView(footer);
        TextView range = Ui.button(this, "日期范围…", false);
        range.setTextSize(10.5f);
        range.setPadding(Ui.dp(this, 10), Ui.dp(this, 7),
                Ui.dp(this, 10), Ui.dp(this, 7));
        range.setOnClickListener(v -> {
            closeDrawer();
            pickDateRange();
        });
        footer.addView(range);
        TextView clear = Ui.text(this, "清除筛选", 10.5f, Ui.DANGER, false);
        clear.setPadding(Ui.dp(this, 12), Ui.dp(this, 6),
                Ui.dp(this, 4), Ui.dp(this, 6));
        clear.setClickable(true);
        clear.setOnClickListener(v -> {
            albumFilter = "";
            dateMode = "";
            rangeStart = 0;
            rangeEnd = 0;
            selected.clear();
            applyFilters();
            closeDrawer();
        });
        footer.addView(clear, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return drawerOverlay;
    }

    private void renderDrawer() {
        if (drawerContent == null) return;
        TextView[] tabs = (TextView[]) drawerPanel.getTag();
        if (tabs != null) {
            styleChip(tabs[0], "date".equals(drawerTab));
            styleChip(tabs[1], "album".equals(drawerTab));
        }
        drawerContent.removeAllViews();
        List<MediaItem> base = tab == 0 ? liveItems : videoItems;
        if ("album".equals(drawerTab)) {
            renderDrawerAlbums(base);
        } else {
            renderDrawerDates(base);
        }
    }

    private void renderDrawerDates(List<MediaItem> base) {
        Map<String, Map<String, List<MediaItem>>> years = new TreeMap<>(
                java.util.Collections.reverseOrder());
        for (MediaItem item : base) {
            Calendar calendar = Calendar.getInstance();
            calendar.setTimeInMillis(item.captureDay());
            String key = new SimpleDateFormat("yyyy-MM", Locale.CHINA)
                    .format(calendar.getTime());
            String year = key.substring(0, 4);
            years.computeIfAbsent(year, value -> new TreeMap<>(
                            java.util.Collections.reverseOrder()))
                    .computeIfAbsent(key, value -> new ArrayList<>())
                    .add(item);
        }
        if (years.isEmpty()) {
            drawerContent.addView(drawerEmpty("没有媒体文件"));
            return;
        }
        for (Map.Entry<String, Map<String, List<MediaItem>>> yearEntry : years.entrySet()) {
            int yearCount = 0;
            long yearBytes = 0;
            for (List<MediaItem> monthItems : yearEntry.getValue().values()) {
                yearCount += monthItems.size();
                for (MediaItem item : monthItems) yearBytes += item.size;
            }
            LinearLayout yearHead = new LinearLayout(this);
            yearHead.setOrientation(LinearLayout.HORIZONTAL);
            yearHead.setGravity(Gravity.CENTER_VERTICAL);
            yearHead.setPadding(Ui.dp(this, 8), Ui.dp(this, 12),
                    Ui.dp(this, 6), Ui.dp(this, 5));
            yearHead.addView(Ui.text(this, yearEntry.getKey() + " 年",
                    14f, Ui.TEXT, true), new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            yearHead.addView(Ui.text(this, yearCount + " 个 · " + Ui.bytes(yearBytes),
                    8.5f, Ui.MUTED, false));
            drawerContent.addView(yearHead);

            for (Map.Entry<String, List<MediaItem>> monthEntry
                    : yearEntry.getValue().entrySet()) {
                String key = monthEntry.getKey();
                List<MediaItem> items = monthEntry.getValue();
                Calendar start = Calendar.getInstance();
                start.set(Integer.parseInt(key.substring(0, 4)),
                        Integer.parseInt(key.substring(5)) - 1, 1, 0, 0, 0);
                start.set(Calendar.MILLISECOND, 0);
                Calendar end = (Calendar) start.clone();
                end.add(Calendar.MONTH, 1);
                end.add(Calendar.MILLISECOND, -1);
                final long monthStart = start.getTimeInMillis();
                final long monthEnd = end.getTimeInMillis();

                LinearLayout row = new LinearLayout(this);
                row.setOrientation(LinearLayout.HORIZONTAL);
                row.setGravity(Gravity.CENTER_VERTICAL);
                row.setPadding(Ui.dp(this, 10), Ui.dp(this, 8),
                        Ui.dp(this, 6), Ui.dp(this, 8));
                row.setBackground(Ui.round(Ui.CARD_2, 9f, this));
                LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                rowParams.bottomMargin = Ui.dp(this, 3);
                row.setLayoutParams(rowParams);

                LinearLayout info = new LinearLayout(this);
                info.setOrientation(LinearLayout.VERTICAL);
                info.setClickable(true);
                info.setOnClickListener(v -> applyDateView(
                        monthStart, monthEnd, "month", key, false, key));
                row.addView(info, new LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                info.addView(Ui.text(this, key.substring(5) + " 月",
                        12.5f, Ui.TEXT, true));
                long bytes = 0;
                int selectedCount = 0;
                for (MediaItem item : items) {
                    bytes += item.size;
                    if (selected.contains(item.path)) selectedCount++;
                }
                info.addView(Ui.text(this, String.format(Locale.CHINA,
                        "%d 个 · %s%s", items.size(), Ui.bytes(bytes),
                        selectedCount > 0 ? " · 已选 " + selectedCount : ""),
                        9f, Ui.MUTED, false));
                TextView select = Ui.text(this, "全选本月", 9.5f, Ui.ACCENT, true);
                select.setPadding(Ui.dp(this, 6), Ui.dp(this, 5),
                        Ui.dp(this, 6), Ui.dp(this, 5));
                select.setClickable(true);
                select.setOnClickListener(v -> applyDateView(
                        monthStart, monthEnd, "month", key, true, key + " 月"));
                row.addView(select);
                drawerContent.addView(row);
            }
            View spacer = new View(this);
            spacer.setLayoutParams(new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 6)));
            drawerContent.addView(spacer);
        }
    }

    private void renderDrawerAlbums(List<MediaItem> base) {
        Map<String, List<MediaItem>> albums = new TreeMap<>();
        for (MediaItem item : base) {
            java.io.File parent = new java.io.File(item.path).getParentFile();
            String key = parent == null ? "/" : parent.getAbsolutePath();
            albums.computeIfAbsent(key, value -> new ArrayList<>()).add(item);
        }
        if (albums.isEmpty()) {
            drawerContent.addView(drawerEmpty("没有相册"));
            return;
        }
        for (Map.Entry<String, List<MediaItem>> entry : albums.entrySet()) {
            String album = entry.getKey();
            List<MediaItem> items = entry.getValue();
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setPadding(Ui.dp(this, 8), Ui.dp(this, 9),
                    Ui.dp(this, 4), Ui.dp(this, 9));
            row.setBackground(Ui.round(Ui.CARD_2, 9f, this));
            LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            rowParams.bottomMargin = Ui.dp(this, 3);
            row.setLayoutParams(rowParams);
            LinearLayout info = new LinearLayout(this);
            info.setOrientation(LinearLayout.VERTICAL);
            info.setClickable(true);
            info.setOnClickListener(v -> applyAlbumView(album, false));
            row.addView(info, new LinearLayout.LayoutParams(
                    0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            info.addView(Ui.text(this, shortAlbum(album), 12f, Ui.TEXT, true));
            long bytes = 0;
            int selectedCount = 0;
            for (MediaItem item : items) {
                bytes += item.size;
                if (selected.contains(item.path)) selectedCount++;
            }
            info.addView(Ui.text(this, String.format(Locale.CHINA,
                    "%d 个 · %s%s", items.size(), Ui.bytes(bytes),
                    selectedCount > 0 ? " · 已选 " + selectedCount : ""),
                    8.5f, Ui.MUTED, false));
            TextView select = Ui.text(this, "全选", 9.5f, Ui.ACCENT, true);
            select.setPadding(Ui.dp(this, 6), Ui.dp(this, 5),
                    Ui.dp(this, 6), Ui.dp(this, 5));
            select.setClickable(true);
            select.setOnClickListener(v -> applyAlbumView(album, true));
            row.addView(select);
            drawerContent.addView(row);
        }
    }

    private TextView drawerEmpty(String text) {
        TextView empty = Ui.text(this, text, 12f, Ui.MUTED, false);
        empty.setGravity(Gravity.CENTER);
        empty.setPadding(0, Ui.dp(this, 60), 0, 0);
        return empty;
    }

    private String shortAlbum(String album) {
        int index = album.lastIndexOf('/');
        String name = index >= 0 ? album.substring(index + 1) : album;
        return name.isEmpty() ? album : name;
    }

    private void applyDateView(long start, long end, String mode,
                               String label, boolean select, String scope) {
        albumFilter = "";
        rangeStart = start;
        rangeEnd = end;
        dateMode = mode;
        if ("compressed".equals(statusFilter)) statusFilter = "all";
        applyFilters();
        if (select) selectDateItems(scope);
        refreshGroupedSelection();
        closeDrawer();
    }

    private void applyAlbumView(String album, boolean select) {
        albumFilter = album;
        rangeStart = 0;
        rangeEnd = 0;
        dateMode = "";
        if ("compressed".equals(statusFilter)) statusFilter = "all";
        applyFilters();
        if (select) selectDateItems("「" + shortAlbum(album) + "」相册");
        closeDrawer();
    }

    private void chooseDateFilter() {
        if (busy || scanning) return;
        boolean active = rangeStart > 0 || rangeEnd > 0;
        String[] options = active
                ? new String[]{"全选某一天", "全选某个月", "选择日期范围", "清除日期筛选"}
                : new String[]{"全选某一天", "全选某个月", "选择日期范围"};
        new AlertDialog.Builder(this)
                .setTitle("按日期全选")
                .setItems(options, (dialog, which) -> {
                    if (which == 0) pickDayFilter();
                    else if (which == 1) pickMonthFilter();
                    else if (which == 2) pickDateRange();
                    else {
                        rangeStart = 0;
                        rangeEnd = 0;
                        dateMode = "";
                        selected.clear();
                        applyFilters();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void pickDayFilter() {
        Calendar calendar = Calendar.getInstance();
        if (rangeStart > 0) calendar.setTimeInMillis(rangeStart);
        DatePickerDialog picker = new DatePickerDialog(this,
                (view, year, month, dayOfMonth) -> {
                    long start = dayMillis(year, month, dayOfMonth);
                    rangeStart = start;
                    rangeEnd = start + 24L * 60 * 60 * 1000 - 1;
                    dateMode = "day";
                    if ("compressed".equals(statusFilter)) statusFilter = "all";
                    applyFilters();
                    selectDateItems(rangeLabel(start));
                },
                calendar.get(Calendar.YEAR), calendar.get(Calendar.MONTH),
                calendar.get(Calendar.DAY_OF_MONTH));
        picker.setTitle("选择要全选的那一天");
        picker.getDatePicker().setMaxDate(System.currentTimeMillis());
        picker.show();
    }

    private void pickMonthFilter() {
        List<MediaItem> base = tab == 0 ? liveItems : videoItems;
        Map<String, Integer> counts = new TreeMap<>(java.util.Collections.reverseOrder());
        for (MediaItem item : base) {
            Calendar calendar = Calendar.getInstance();
            calendar.setTimeInMillis(captureDay(item));
            String key = new SimpleDateFormat("yyyy-MM", Locale.CHINA)
                    .format(calendar.getTime());
            counts.put(key, counts.getOrDefault(key, 0) + 1);
        }
        if (counts.isEmpty()) {
            toast("当前分类没有可选择的月份");
            return;
        }
        final String[] labels = new String[counts.size()];
        final String[] keys = new String[counts.size()];
        int index = 0;
        for (Map.Entry<String, Integer> entry : counts.entrySet()) {
            keys[index] = entry.getKey();
            labels[index] = entry.getKey() + " · " + entry.getValue() + " 个";
            index++;
        }
        new AlertDialog.Builder(this)
                .setTitle("选择月份（自动全选未压缩）")
                .setItems(labels, (dialog, which) -> {
                    String key = keys[which];
                    String[] parts = key.split("-");
                    Calendar start = Calendar.getInstance();
                    start.set(Integer.parseInt(parts[0]),
                            Integer.parseInt(parts[1]) - 1, 1, 0, 0, 0);
                    start.set(Calendar.MILLISECOND, 0);
                    Calendar end = (Calendar) start.clone();
                    end.add(Calendar.MONTH, 1);
                    end.add(Calendar.MILLISECOND, -1);
                    rangeStart = start.getTimeInMillis();
                    rangeEnd = end.getTimeInMillis();
                    dateMode = "month";
                    if ("compressed".equals(statusFilter)) statusFilter = "all";
                    applyFilters();
                    selectDateItems(key + " 月");
                })
                .setNegativeButton("取消", null)
                .show();
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
                                albumFilter = "";
                                rangeStart = Math.min(start, end);
                                rangeEnd = Math.max(start, end);
                                dateMode = "range";
                                if ("compressed".equals(statusFilter)) statusFilter = "all";
                                applyFilters();
                                selectDateItems("日期范围");
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

    private void selectDateItems(String scope) {
        int selectedCount = 0;
        int skipped = 0;
        for (MediaItem item : displayedItems) {
            if (item.compressed) {
                skipped++;
            } else {
                selected.add(item.path);
                selectedCount++;
            }
        }
        adapter.refresh();
        updateSelection();
        refreshGroupedSelection();
        if (selectedCount > 0) {
            hint.setText(String.format(Locale.CHINA,
                    "已选中%s %d 个未压缩文件%s；预览确认后再应用。",
                    scope, selectedCount,
                    skipped > 0 ? "，跳过 " + skipped + " 个已压缩" : ""));
            toast(String.format(Locale.CHINA,
                    "已全选%s的 %d 个未压缩文件%s",
                    scope, selectedCount,
                    skipped > 0 ? "，跳过 " + skipped + " 个已压缩" : ""));
        } else {
            hint.setText(skipped > 0
                    ? scope + "范围内都是已压缩文件。"
                    : scope + "范围内没有可压缩文件。");
            toast(hint.getText().toString());
        }
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
        if (emptyView != null) {
            emptyView.setText("正在扫描媒体库…");
            emptyView.setVisibility(View.VISIBLE);
            listView.setVisibility(View.GONE);
            if (groupedList != null) groupedList.setVisibility(View.GONE);
        }
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
        refreshModeStats();
        showProgress(false, false, "");
        applyFilters();
        showEmptyState();
        Log.d(TAG, "scan done live=" + liveItems.size() + " videos=" + videoItems.size());
    }

    private void markCompressed(MediaItem item) {
        Storage.HistoryEntry entry = history.get(item.path);
        item.compressed = entry != null && entry.compressedSize == item.size;
    }

    private void refreshGroupedSelection() {
        if (groupedAdapter != null && "month".equals(dateMode)) groupedAdapter.refresh();
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
