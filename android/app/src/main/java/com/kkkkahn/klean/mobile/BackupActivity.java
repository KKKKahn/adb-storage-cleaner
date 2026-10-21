package com.kkkkahn.klean.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.CheckBox;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class BackupActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Set<String> selected = new LinkedHashSet<>();
    private final List<Storage.BackupBatch> batches = new ArrayList<>();

    private ThumbnailLoader thumbnails;
    private LinearLayout content;
    private TextView summary;
    private TextView selectionInfo;
    private TextView restoreButton;
    private TextView deleteButton;
    private TextView progressText;
    private ProgressBar progressBar;
    private View progressPanel;
    private boolean busy;
    private boolean changed;
    private boolean liveMode = true;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        liveMode = getIntent().getBooleanExtra("live", true);
        thumbnails = new ThumbnailLoader(this);
        FrameLayout root = new FrameLayout(this);
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

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        root.addView(page, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        page.addView(buildHeader());
        page.addView(buildProgress());

        ScrollView scroll = new ScrollView(this);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(Ui.dp(this, 16), Ui.dp(this, 4),
                Ui.dp(this, 16), Ui.dp(this, 12));
        scroll.addView(content);
        page.addView(scroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        page.addView(buildBottomBar());
        load();
    }

    @Override
    public void onBackPressed() {
        if (changed) setResult(RESULT_OK);
        finish();
    }

    @Override
    protected void onDestroy() {
        io.shutdownNow();
        thumbnails.shutdown();
        super.onDestroy();
    }

    private View buildHeader() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(Ui.dp(this, 18), Ui.dp(this, 10),
                Ui.dp(this, 18), Ui.dp(this, 4));
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        header.addView(top);

        TextView back = Ui.text(this, "←", 22f, Ui.TEXT, true);
        back.setGravity(Gravity.CENTER);
        back.setPadding(Ui.dp(this, 2), 0, Ui.dp(this, 12), 0);
        back.setClickable(true);
        back.setOnClickListener(v -> {
            if (changed) setResult(RESULT_OK);
            finish();
        });
        top.addView(back);
        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        top.addView(titles, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        titles.addView(Ui.text(this, liveMode ? "实况原片备份" : "视频原片备份", 20f, Ui.TEXT, true));
        summary = Ui.text(this, "正在读取备份…", 10f, Ui.MUTED, false);
        summary.setPadding(0, Ui.dp(this, 3), 0, 0);
        titles.addView(summary);

        TextView deleteAll = Ui.text(this, "删除全部", 10.5f, Ui.DANGER, false);
        deleteAll.setPadding(Ui.dp(this, 8), Ui.dp(this, 6),
                Ui.dp(this, 8), Ui.dp(this, 6));
        deleteAll.setClickable(true);
        deleteAll.setOnClickListener(v -> confirmDeleteAll());
        top.addView(deleteAll);
        return header;
    }

    private View buildProgress() {
        progressPanel = new LinearLayout(this);
        ((LinearLayout) progressPanel).setOrientation(LinearLayout.VERTICAL);
        progressPanel.setPadding(Ui.dp(this, 16), Ui.dp(this, 8),
                Ui.dp(this, 16), Ui.dp(this, 8));
        progressPanel.setBackground(Ui.round(Ui.CARD, 12f, this));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(Ui.dp(this, 16), Ui.dp(this, 4),
                Ui.dp(this, 16), Ui.dp(this, 4));
        progressPanel.setLayoutParams(params);
        progressBar = new ProgressBar(this, null,
                android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setProgressTintList(ColorStateList.valueOf(Ui.ACCENT));
        progressBar.setProgressBackgroundTintList(ColorStateList.valueOf(0xFF252C42));
        ((LinearLayout) progressPanel).addView(progressBar,
                new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, Ui.dp(this, 5)));
        progressText = Ui.text(this, "", 10.5f, Ui.MUTED, false);
        progressText.setPadding(0, Ui.dp(this, 5), 0, 0);
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
        selectionInfo = Ui.text(this, "已选 0 个 · 0 B", 11.5f, Ui.TEXT, true);
        bar.addView(selectionInfo, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        restoreButton = Ui.button(this, "恢复所选", false);
        restoreButton.setTextSize(11f);
        restoreButton.setPadding(Ui.dp(this, 10), Ui.dp(this, 8),
                Ui.dp(this, 10), Ui.dp(this, 8));
        restoreButton.setOnClickListener(v -> restoreSelected());
        bar.addView(restoreButton);
        deleteButton = Ui.button(this, "删除所选", false);
        deleteButton.setTextSize(11f);
        deleteButton.setPadding(Ui.dp(this, 10), Ui.dp(this, 8),
                Ui.dp(this, 10), Ui.dp(this, 8));
        LinearLayout.LayoutParams deleteParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        deleteParams.setMarginStart(Ui.dp(this, 6));
        bar.addView(deleteButton, deleteParams);
        deleteButton.setOnClickListener(v -> confirmDeleteSelected());
        updateSelection();
        return bar;
    }

    private void load() {
        busy = true;
        setBusy(true);
        progressPanel.setVisibility(View.VISIBLE);
        progressBar.setIndeterminate(true);
        progressText.setText("正在读取备份…");
        io.execute(() -> {
            List<Storage.BackupBatch> loaded = Storage.loadBackups(this);
            List<Storage.BackupBatch> filtered = new ArrayList<>();
            for (Storage.BackupBatch batch : loaded) {
                java.util.Iterator<Storage.BackupRecord> iterator = batch.entries.iterator();
                while (iterator.hasNext()) {
                    Storage.BackupRecord record = iterator.next();
                    if (record.live != liveMode) iterator.remove();
                }
                if (!batch.entries.isEmpty()) filtered.add(batch);
            }
            main.post(() -> {
                batches.clear();
                batches.addAll(filtered);
                selected.clear();
                busy = false;
                setBusy(false);
                progressPanel.setVisibility(View.GONE);
                render();
            });
        });
    }

    private void render() {
        content.removeAllViews();
        int count = 0;
        long bytes = 0;
        for (Storage.BackupBatch batch : batches) {
            count += batch.entries.size();
            bytes += batch.bytes();
        }
        summary.setText(String.format(Locale.CHINA,
                "%d 个原片 · %s · App 私有备份（卸载 App 会丢失）", count, Ui.bytes(bytes)));
        if (batches.isEmpty()) {
            TextView empty = Ui.text(this,
                    liveMode ? "还没有实况原片备份。\n应用压缩结果前会自动备份到这里。"
                            : "还没有视频原片备份。\n应用压缩结果前会自动备份到这里。",
                    13f, Ui.MUTED, false);
            empty.setGravity(Gravity.CENTER);
            empty.setPadding(0, Ui.dp(this, 80), 0, 0);
            content.addView(empty);
            updateSelection();
            return;
        }
        for (Storage.BackupBatch batch : batches) content.addView(buildBatchCard(batch));
        updateSelection();
    }

    private View buildBatchCard(Storage.BackupBatch batch) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(Ui.dp(this, 12), Ui.dp(this, 10),
                Ui.dp(this, 12), Ui.dp(this, 8));
        card.setBackground(Ui.bordered(Ui.CARD, 12f, Ui.BORDER, this));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.bottomMargin = Ui.dp(this, 10);
        card.setLayoutParams(params);

        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        card.addView(head);
        LinearLayout headText = new LinearLayout(this);
        headText.setOrientation(LinearLayout.VERTICAL);
        head.addView(headText, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        headText.addView(Ui.text(this, batchLabel(batch), 12.5f, Ui.TEXT, true));
        headText.addView(Ui.text(this, batch.entries.size() + " 个 · "
                + Ui.bytes(batch.bytes()), 9.5f, Ui.MUTED, false));

        TextView select = smallButton("全选本批", false);
        select.setOnClickListener(v -> {
            boolean all = allSelected(batch);
            for (Storage.BackupRecord record : batch.entries) {
                String key = key(batch, record);
                if (all) selected.remove(key);
                else selected.add(key);
            }
            render();
        });
        head.addView(select);
        TextView restore = smallButton("恢复本批", false);
        restore.setOnClickListener(v -> confirmRestore(new ArrayList<>(batch.entries)));
        head.addView(restore);
        TextView delete = smallButton("删除本批", true);
        delete.setOnClickListener(v -> confirmDelete(new ArrayList<>(batch.entries)));
        head.addView(delete);

        TextView hint = Ui.text(this,
                "原片备份 · 可预览、恢复或删除；删除不可撤销",
                9f, Ui.MUTED, false);
        hint.setPadding(0, Ui.dp(this, 5), 0, Ui.dp(this, 3));
        card.addView(hint);

        for (Storage.BackupRecord record : batch.entries) {
            card.addView(buildRow(batch, record));
        }
        return card;
    }

    private View buildRow(Storage.BackupBatch batch, Storage.BackupRecord record) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, Ui.dp(this, 5), 0, Ui.dp(this, 5));
        String key = key(batch, record);

        CheckBox check = new CheckBox(this);
        check.setChecked(selected.contains(key));
        check.setClickable(false);
        check.setFocusable(false);
        check.setButtonTintList(new ColorStateList(
                new int[][]{
                        new int[]{android.R.attr.state_checked},
                        new int[]{}
                },
                new int[]{Ui.ACCENT, 0xFF59627C}));
        row.addView(check, new LinearLayout.LayoutParams(
                Ui.dp(this, 30), Ui.dp(this, 38)));

        ImageView thumb = new ImageView(this);
        thumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        thumb.setBackground(Ui.round(0xFF20263A, 6f, this));
        thumb.setClipToOutline(true);
        thumb.setOutlineProvider(new android.view.ViewOutlineProvider() {
            @Override
            public void getOutline(View view, android.graphics.Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(),
                        Ui.dp(BackupActivity.this, 6));
            }
        });
        row.addView(thumb, new LinearLayout.LayoutParams(
                Ui.dp(this, 52), Ui.dp(this, 39)));
        File file = record.file();
        if (file != null) {
            thumbnails.loadFile("backup:" + key, file, thumb);
        }
        thumb.setOnClickListener(v -> {
            if (file != null) preview(file, record.path);
        });

        LinearLayout texts = new LinearLayout(this);
        texts.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams textParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        textParams.setMarginStart(Ui.dp(this, 9));
        row.addView(texts, textParams);
        String name = record.path.substring(record.path.lastIndexOf('/') + 1);
        TextView title = Ui.text(this, name, 10.5f, Ui.TEXT, false);
        title.setSingleLine(true);
        title.setEllipsize(TextUtils.TruncateAt.MIDDLE);
        texts.addView(title);
        String kind = record.live ? "实况" : "视频";
        String date = new java.text.SimpleDateFormat("MM-dd HH:mm", Locale.CHINA)
                .format(new Date(record.mtime > 0 ? record.mtime : System.currentTimeMillis()));
        texts.addView(Ui.text(this, String.format(Locale.CHINA,
                "%s · %s · %s", date, kind, Ui.bytes(record.originalSize)),
                9f, Ui.MUTED, false));

        TextView preview = Ui.text(this, "预览", 10f, Ui.ACCENT, true);
        preview.setPadding(Ui.dp(this, 7), Ui.dp(this, 4),
                Ui.dp(this, 7), Ui.dp(this, 4));
        preview.setClickable(true);
        preview.setOnClickListener(v -> {
            if (file != null) preview(file, record.path);
        });
        row.addView(preview);

        row.setOnClickListener(v -> {
            if (selected.contains(key)) selected.remove(key);
            else selected.add(key);
            check.setChecked(selected.contains(key));
            updateSelection();
        });
        return row;
    }

    private TextView smallButton(String text, boolean danger) {
        TextView view = Ui.text(this, text, 9.5f, danger ? Ui.DANGER : Ui.MUTED, false);
        view.setPadding(Ui.dp(this, 6), Ui.dp(this, 4),
                Ui.dp(this, 6), Ui.dp(this, 4));
        view.setClickable(true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMarginStart(Ui.dp(this, 4));
        view.setLayoutParams(params);
        return view;
    }

    private String key(Storage.BackupBatch batch, Storage.BackupRecord record) {
        return batch.id + "|" + record.backup;
    }

    private boolean allSelected(Storage.BackupBatch batch) {
        if (batch.entries.isEmpty()) return false;
        for (Storage.BackupRecord record : batch.entries) {
            if (!selected.contains(key(batch, record))) return false;
        }
        return true;
    }

    private void updateSelection() {
        if (selectionInfo == null) return;
        long bytes = 0;
        int count = 0;
        for (Storage.BackupBatch batch : batches) {
            for (Storage.BackupRecord record : batch.entries) {
                if (selected.contains(key(batch, record))) {
                    count++;
                    bytes += record.originalSize;
                }
            }
        }
        selectionInfo.setText(String.format(Locale.CHINA,
                "已选 %d 个 · %s", count, Ui.bytes(bytes)));
        boolean enabled = count > 0 && !busy;
        restoreButton.setEnabled(enabled);
        restoreButton.setAlpha(enabled ? 1f : 0.45f);
        deleteButton.setEnabled(enabled);
        deleteButton.setAlpha(enabled ? 1f : 0.45f);
    }

    private void setBusy(boolean value) {
        busy = value;
        updateSelection();
    }

    private List<Storage.BackupRecord> selectedRecords() {
        List<Storage.BackupRecord> records = new ArrayList<>();
        for (Storage.BackupBatch batch : batches) {
            for (Storage.BackupRecord record : batch.entries) {
                if (selected.contains(key(batch, record))) records.add(record);
            }
        }
        return records;
    }

    private List<Storage.BackupRecord> allRecords() {
        List<Storage.BackupRecord> records = new ArrayList<>();
        for (Storage.BackupBatch batch : batches) records.addAll(batch.entries);
        return records;
    }

    private void restoreSelected() {
        List<Storage.BackupRecord> records = selectedRecords();
        if (records.isEmpty()) return;
        confirmRestore(records);
    }

    private void confirmRestore(List<Storage.BackupRecord> records) {
        if (records.isEmpty()) return;
        new AlertDialog.Builder(this)
                .setTitle("恢复原片？")
                .setMessage("将把 " + records.size() + " 个备份恢复回手机。\n\n"
                        + "只有当前仍是本工具生成的压缩版时才会替换；"
                        + "被其他方式修改过的文件会跳过。JPG 不会改动。")
                .setNegativeButton("取消", null)
                .setPositiveButton("恢复", (dialog, which) -> runRestore(records))
                .show();
    }

    private void runRestore(List<Storage.BackupRecord> records) {
        setBusy(true);
        progressPanel.setVisibility(View.VISIBLE);
        progressBar.setIndeterminate(false);
        progressBar.setMax(records.size());
        progressBar.setProgress(0);
        progressText.setText("准备恢复…");
        io.execute(() -> {
            int restored = 0;
            int skipped = 0;
            int index = 0;
            for (Storage.BackupRecord record : records) {
                final int current = index;
                main.post(() -> {
                    progressBar.setProgress(current);
                    progressText.setText(String.format(Locale.CHINA,
                            "%d/%d · 恢复 %s", current + 1, records.size(),
                            record.path.substring(record.path.lastIndexOf('/') + 1)));
                });
                try {
                    Storage.restore(this, record);
                    restored++;
                } catch (Exception error) {
                    skipped++;
                }
                index++;
            }
            final int restoredCount = restored;
            final int skippedCount = skipped;
            main.post(() -> {
                setBusy(false);
                changed = true;
                setResult(RESULT_OK);
                progressPanel.setVisibility(View.GONE);
                load();
                new AlertDialog.Builder(this)
                        .setTitle("恢复完成")
                        .setMessage("已恢复 " + restoredCount + " 个，跳过 "
                                + skippedCount + " 个。\n跳过的文件可能已被修改或已经恢复。")
                        .setPositiveButton("知道了", null)
                        .show();
            });
        });
    }

    private void confirmDeleteSelected() {
        List<Storage.BackupRecord> records = selectedRecords();
        if (!records.isEmpty()) confirmDelete(records);
    }

    private void confirmDelete(List<Storage.BackupRecord> records) {
        if (records.isEmpty()) return;
        new AlertDialog.Builder(this)
                .setTitle("删除备份？")
                .setMessage("将永久删除 " + records.size() + " 个原片备份，无法撤销。\n"
                        + "手机上的压缩版不会被删除。")
                .setNegativeButton("取消", null)
                .setPositiveButton("删除", (dialog, which) -> runDelete(records))
                .show();
    }

    private void confirmDeleteAll() {
        List<Storage.BackupRecord> records = allRecords();
        if (records.isEmpty()) {
            Toast.makeText(this, "当前没有备份", Toast.LENGTH_SHORT).show();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("删除全部备份？")
                .setMessage("将永久删除 " + records.size() + " 个原片备份，无法撤销。\n"
                        + "手机上的压缩版不会被删除。")
                .setNegativeButton("取消", null)
                .setPositiveButton("删除全部", (dialog, which) -> runDelete(records))
                .show();
    }

    private void runDelete(List<Storage.BackupRecord> records) {
        setBusy(true);
        progressPanel.setVisibility(View.VISIBLE);
        progressBar.setIndeterminate(true);
        progressText.setText("正在删除备份…");
        io.execute(() -> {
            Storage.deleteBackups(this, records);
            main.post(() -> {
                setBusy(false);
                changed = true;
                setResult(RESULT_OK);
                progressPanel.setVisibility(View.GONE);
                load();
                Toast.makeText(this, "已删除备份", Toast.LENGTH_SHORT).show();
            });
        });
    }

    private String batchLabel(Storage.BackupBatch batch) {
        try {
            Date date = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
                    .parse(batch.id.substring(0, 15));
            return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA)
                    .format(date);
        } catch (Exception error) {
            return batch.id;
        }
    }

    private void preview(File file, String title) {
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
            Toast.makeText(this, "预览失败：" + error.getMessage(),
                    Toast.LENGTH_LONG).show();
        }
    }
}
