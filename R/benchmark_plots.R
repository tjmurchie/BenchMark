#!/usr/bin/env Rscript
# BenchMark -publication-quality comparison plots
# Usage: Rscript benchmark_plots.R <input.csv> [output_dir] [title]

suppressWarnings(suppressPackageStartupMessages({
  required <- c("ggplot2", "dplyr", "tidyr", "scales", "gridExtra", "RColorBrewer")
  missing  <- required[!sapply(required, requireNamespace, quietly = TRUE)]
  if (length(missing) > 0) {
    cat("Installing missing R packages:", paste(missing, collapse = ", "), "\n")
    install.packages(missing, repos = "https://cloud.r-project.org", quiet = TRUE)
  }
  library(ggplot2)
  library(dplyr)
  library(tidyr)
  library(scales)
  library(gridExtra)
  library(RColorBrewer)
}))

args        <- commandArgs(trailingOnly = TRUE)
input_csv   <- if (length(args) >= 1) args[1] else stop("Usage: Rscript benchmark_plots.R <csv> [outdir] [title]")
output_dir  <- if (length(args) >= 2) args[2] else dirname(input_csv)
plot_title  <- if (length(args) >= 3) args[3] else "Metagenomic Classifier Benchmark Comparison"

dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

# ── Load data ────────────────────────────────────────────────────────────
df_raw <- read.csv(input_csv, stringsAsFactors = FALSE)

# Coerce numeric columns
num_cols <- c("wall_time_s", "cpu_user_s", "cpu_system_s", "cpu_total_s",
              "cpu_efficiency", "peak_mem_mb", "avg_mem_mb",
              "disk_read_mb", "disk_write_mb", "total_io_mb",
              "max_threads", "peak_processes", "total_ram_gb")
for (col in intersect(num_cols, names(df_raw))) {
  df_raw[[col]] <- suppressWarnings(as.numeric(df_raw[[col]]))
}

df_raw$is_summary <- as.logical(df_raw$is_summary)

# Summary rows only (TOTAL per tool/dataset run) for cross-tool comparisons
df_total <- df_raw %>% filter(is_summary == TRUE)

# Step rows for per-step breakdown
df_steps <- df_raw %>% filter(is_summary == FALSE)

if (nrow(df_total) == 0) {
  cat("WARNING: No TOTAL summary rows found. Falling back to all rows.\n")
  df_total <- df_raw
}

# Colour palette -enough for many tools
n_tools <- length(unique(df_total$tool_name))
if (n_tools <= 8) {
  pal <- brewer.pal(max(3, n_tools), "Set2")
} else {
  pal <- colorRampPalette(brewer.pal(8, "Set2"))(n_tools)
}

# Shared ggplot theme for publication figures
pub_theme <- theme_bw(base_size = 11) +
  theme(
    panel.grid.minor  = element_blank(),
    strip.background  = element_rect(fill = "grey92", colour = "grey70"),
    legend.position   = "bottom",
    plot.title        = element_text(face = "bold", size = 12),
    plot.subtitle     = element_text(size = 10, colour = "grey40"),
    axis.title        = element_text(size = 10),
  )

label_tool_dataset <- function(df) {
  if ("dataset" %in% names(df) && length(unique(df$dataset)) > 1) {
    df$tool_label <- paste0(df$tool_name, "\n(", df$dataset, ")")
  } else {
    df$tool_label <- df$tool_name
  }
  df
}

df_total <- label_tool_dataset(df_total)

# ── Helper: time formatter ───────────────────────────────────────────────
fmt_time <- function(seconds) {
  ifelse(seconds < 60, paste0(round(seconds, 0), "s"),
  ifelse(seconds < 3600, paste0(round(seconds/60, 1), "m"),
                         paste0(round(seconds/3600, 2), "h")))
}

# ─────────────────────────────────────────────────────────────────────────
# PLOT 1 -Wall-clock time comparison
# ─────────────────────────────────────────────────────────────────────────
p_wall <- ggplot(df_total, aes(x = reorder(tool_label, wall_time_s), y = wall_time_s / 60,
                               fill = tool_name)) +
  geom_col(width = 0.65, colour = "white") +
  geom_text(aes(label = fmt_time(wall_time_s)),
            hjust = -0.1, size = 3.2, colour = "grey30") +
  coord_flip() +
  scale_fill_manual(values = pal, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0, 0.15)),
                     labels = function(x) paste0(x, "m")) +
  labs(title = "Active Wall-Clock Time",
       subtitle = "Time spent actively running (idle/waiting excluded)",
       x = NULL, y = "Minutes") +
  pub_theme

# ─────────────────────────────────────────────────────────────────────────
# PLOT 2 -CPU time (shows multi-threading)
# ─────────────────────────────────────────────────────────────────────────
p_cpu <- ggplot(df_total, aes(x = reorder(tool_label, cpu_total_s), y = cpu_total_s / 60,
                              fill = tool_name)) +
  geom_col(width = 0.65, colour = "white") +
  geom_text(aes(label = fmt_time(cpu_total_s)),
            hjust = -0.1, size = 3.2, colour = "grey30") +
  coord_flip() +
  scale_fill_manual(values = pal, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0, 0.15)),
                     labels = function(x) paste0(x, "m")) +
  labs(title = "Total CPU Time",
       subtitle = "Summed across all threads (user + system)",
       x = NULL, y = "CPU-minutes") +
  pub_theme

# ─────────────────────────────────────────────────────────────────────────
# PLOT 3 -Peak memory
# ─────────────────────────────────────────────────────────────────────────
df_total$peak_mem_gb <- df_total$peak_mem_mb / 1024

p_mem <- ggplot(df_total, aes(x = reorder(tool_label, peak_mem_gb), y = peak_mem_gb,
                              fill = tool_name)) +
  geom_col(width = 0.65, colour = "white") +
  geom_text(aes(label = paste0(round(peak_mem_gb, 1), " GB")),
            hjust = -0.1, size = 3.2, colour = "grey30") +
  coord_flip() +
  scale_fill_manual(values = pal, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0, 0.18))) +
  labs(title = "Peak Memory Usage",
       subtitle = "Maximum RSS across all steps",
       x = NULL, y = "GB") +
  pub_theme

# ─────────────────────────────────────────────────────────────────────────
# PLOT 4 -CPU efficiency (parallelism ratio)
# ─────────────────────────────────────────────────────────────────────────
p_eff <- ggplot(df_total, aes(x = reorder(tool_label, cpu_efficiency), y = cpu_efficiency,
                              fill = tool_name)) +
  geom_col(width = 0.65, colour = "white") +
  geom_hline(yintercept = 1, linetype = "dashed", colour = "grey50", linewidth = 0.5) +
  geom_text(aes(label = round(cpu_efficiency, 2)),
            hjust = -0.1, size = 3.2, colour = "grey30") +
  coord_flip() +
  scale_fill_manual(values = pal, guide = "none") +
  scale_y_continuous(expand = expansion(mult = c(0, 0.15))) +
  labs(title = "CPU Efficiency (Parallelism Ratio)",
       subtitle = "CPU time / wall time -values >1 indicate multi-threading",
       x = NULL, y = "CPU-time / wall-time") +
  pub_theme

# ─────────────────────────────────────────────────────────────────────────
# PLOT 5 -I/O footprint
# ─────────────────────────────────────────────────────────────────────────
if ("total_io_mb" %in% names(df_total) && any(!is.na(df_total$total_io_mb))) {
  df_io <- df_total %>%
    select(tool_label, tool_name, disk_read_mb, disk_write_mb) %>%
    pivot_longer(cols = c(disk_read_mb, disk_write_mb),
                 names_to = "io_type", values_to = "mb") %>%
    mutate(io_type = recode(io_type, disk_read_mb = "Read", disk_write_mb = "Write"),
           gb = mb / 1024)

  p_io <- ggplot(df_io, aes(x = tool_label, y = gb, fill = io_type)) +
    geom_col(position = "dodge", width = 0.65, colour = "white") +
    scale_fill_manual(values = c("Read" = "#4393c3", "Write" = "#d6604d"),
                      name = "I/O type") +
    scale_y_continuous(expand = expansion(mult = c(0, 0.12))) +
    labs(title = "Disk I/O",
         subtitle = "Total data read and written during active steps",
         x = NULL, y = "GB") +
    pub_theme +
    theme(axis.text.x = element_text(angle = 30, hjust = 1))
} else {
  p_io <- NULL
}

# ─────────────────────────────────────────────────────────────────────────
# PLOT 6 -Per-step breakdown (if multiple tools have steps)
# ─────────────────────────────────────────────────────────────────────────
if (nrow(df_steps) > 0) {
  df_steps <- label_tool_dataset(df_steps)
  df_steps$step_name <- factor(df_steps$step_name,
                                levels = rev(unique(df_steps$step_name)))

  p_steps <- ggplot(df_steps, aes(x = tool_label, y = wall_time_s / 60,
                                  fill = step_name)) +
    geom_col(width = 0.65, colour = "white") +
    scale_fill_brewer(palette = "Paired", name = "Step") +
    scale_y_continuous(expand = expansion(mult = c(0, 0.1)),
                       labels = function(x) paste0(x, "m")) +
    labs(title = "Wall-Time per Step",
         subtitle = "Each segment = one auto-detected or labelled pipeline step",
         x = NULL, y = "Minutes") +
    pub_theme +
    theme(axis.text.x = element_text(angle = 30, hjust = 1))
} else {
  p_steps <- NULL
}

# ─────────────────────────────────────────────────────────────────────────
# PLOT 7 -Resource overview scatter: speed vs. memory
# ─────────────────────────────────────────────────────────────────────────
tool_label_geom <- if (requireNamespace("ggrepel", quietly = TRUE)) {
  ggrepel::geom_text_repel(size = 3.2, colour = "grey30", max.overlaps = 20)
} else {
  geom_text(vjust = -0.8, size = 3.2, colour = "grey30")
}

p_scatter <- ggplot(df_total,
                    aes(x = wall_time_s / 60, y = peak_mem_mb / 1024,
                        colour = tool_name, label = tool_name)) +
  geom_point(size = 4, alpha = 0.85) +
  tool_label_geom +
  scale_colour_manual(values = pal, guide = "none") +
  labs(title = "Speed vs. Memory (lower-left = most efficient)",
       x = "Wall time (minutes)", y = "Peak memory (GB)") +
  pub_theme

# ─────────────────────────────────────────────────────────────────────────
# PLOT 8 -Normalised radar/heatmap overview table
# ─────────────────────────────────────────────────────────────────────────
norm_cols <- c("wall_time_s", "cpu_total_s", "peak_mem_mb", "disk_read_mb",
               "disk_write_mb", "max_threads")
norm_cols <- intersect(norm_cols, names(df_total))

df_heat <- df_total %>%
  select(tool_label, all_of(norm_cols)) %>%
  mutate(across(all_of(norm_cols), ~ as.numeric(.x))) %>%
  mutate(across(all_of(norm_cols), ~ {
    mx <- max(.x, na.rm = TRUE)
    if (mx > 0) .x / mx else .x
  })) %>%
  pivot_longer(-tool_label, names_to = "metric", values_to = "norm_value") %>%
  mutate(metric = recode(metric,
    wall_time_s   = "Wall time",
    cpu_total_s   = "CPU time",
    peak_mem_mb   = "Peak memory",
    disk_read_mb  = "Disk read",
    disk_write_mb = "Disk write",
    max_threads   = "Max threads"
  ))

p_heat <- ggplot(df_heat, aes(x = metric, y = tool_label, fill = norm_value)) +
  geom_tile(colour = "white", linewidth = 0.5) +
  geom_text(aes(label = round(norm_value, 2)), size = 3, colour = "grey20") +
  scale_fill_gradient2(low = "#1a9850", mid = "#ffffbf", high = "#d73027",
                       midpoint = 0.5, limits = c(0, 1), name = "Relative\n(0=best)") +
  scale_x_discrete(position = "top") +
  labs(title = "Normalised Resource Overview",
       subtitle = "Each metric scaled 0-1 (1 = highest resource use in group)",
       x = NULL, y = NULL) +
  pub_theme +
  theme(axis.text.x = element_text(angle = 30, hjust = 0))

# ─────────────────────────────────────────────────────────────────────────
# Write output
# ─────────────────────────────────────────────────────────────────────────
pdf_path <- file.path(output_dir, "benchmark_comparison.pdf")
pdf(pdf_path, width = 10, height = 6.5, useDingbats = FALSE)

# Title page
grid::grid.newpage()
grid::grid.text(plot_title,
                x = 0.5, y = 0.55, just = "centre",
                gp = grid::gpar(fontsize = 18, fontface = "bold"))
grid::grid.text(paste("Generated:", format(Sys.time(), "%Y-%m-%d %H:%M")),
                x = 0.5, y = 0.45, just = "centre",
                gp = grid::gpar(fontsize = 11, col = "grey40"))
grid::grid.text(paste("Input:", basename(input_csv)),
                x = 0.5, y = 0.40, just = "centre",
                gp = grid::gpar(fontsize = 9, col = "grey50"))

print(p_wall)
print(p_cpu)
print(p_mem)
print(p_eff)
if (!is.null(p_io))    print(p_io)
if (!is.null(p_steps)) print(p_steps)
print(p_scatter)
print(p_heat)

# Combined overview panel (wall, cpu, mem, eff)
grid.arrange(p_wall, p_cpu, p_mem, p_eff, ncol = 2,
             top = grid::textGrob(paste(plot_title, "— Resource Summary"),
                                  gp = grid::gpar(fontsize = 11, fontface = "bold")))

dev.off()

# Also write individual PNGs for easy embedding
plots_named <- list(
  "01_wall_time"    = p_wall,
  "02_cpu_time"     = p_cpu,
  "03_peak_memory"  = p_mem,
  "04_cpu_efficiency" = p_eff,
  "05_resource_heatmap" = p_heat,
  "06_speed_vs_memory"  = p_scatter
)
if (!is.null(p_steps)) plots_named[["07_step_breakdown"]] <- p_steps
if (!is.null(p_io))    plots_named[["08_io_footprint"]]   <- p_io

for (nm in names(plots_named)) {
  png_path <- file.path(output_dir, paste0(nm, ".png"))
  ggsave(png_path, plot = plots_named[[nm]], width = 8, height = 5, dpi = 300)
}

# Summary table CSV
summary_cols <- c("tool_name", "dataset", "run_date",
                  "wall_time_s", "cpu_total_s", "cpu_efficiency",
                  "peak_mem_mb", "avg_mem_mb", "max_threads",
                  "disk_read_mb", "disk_write_mb")
summary_cols <- intersect(summary_cols, names(df_total))
summary_table <- df_total %>% select(all_of(summary_cols)) %>%
  arrange(wall_time_s)
write.csv(summary_table, file.path(output_dir, "benchmark_summary_table.csv"),
          row.names = FALSE)

cat(sprintf("\nPlots written to: %s\n", output_dir))
cat(sprintf("  PDF  : %s\n", pdf_path))
cat(sprintf("  PNGs : %d individual plot files\n", length(plots_named)))
cat(sprintf("  Table: benchmark_summary_table.csv\n"))
cat(sprintf("  Tools: %s\n", paste(unique(df_total$tool_name), collapse = ", ")))
cat(sprintf("  Rows : %d total, %d summary\n", nrow(df_raw), nrow(df_total)))
