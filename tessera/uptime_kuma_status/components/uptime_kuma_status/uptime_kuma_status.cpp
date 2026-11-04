#include "uptime_kuma_status.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string>

namespace esphome::uptime_kuma_status {

using tessera::Font;
using tessera::Tone;
namespace ui = tessera::ui;

// The status of one monitor, as the Uptime Kuma integration's status sensor says it (an enum: up, down, pending,
// maintenance), or NO_DATA while the sensor is unavailable, unknown, of another kind, or not sent yet. The order is
// how much a person needs to see it: a monitor that is down first, one that is up last.
enum class Status : uint8_t { DOWN, PENDING, MAINTENANCE, NO_DATA, UP };

static Status status_of(const char *state) {
  if (!strcmp(state, "up")) return Status::UP;
  if (!strcmp(state, "down")) return Status::DOWN;
  if (!strcmp(state, "pending")) return Status::PENDING;
  if (!strcmp(state, "maintenance")) return Status::MAINTENANCE;
  return Status::NO_DATA;
}

static Tone tone_of(Status s) {
  switch (s) {
    case Status::UP: return Tone::GOOD;
    case Status::DOWN: return Tone::ALERT;
    case Status::PENDING: return Tone::BUSY;
    case Status::MAINTENANCE: return Tone::ACCENT;
    default: return Tone::NORMAL;
  }
}

static const char *word_of(Status s) {
  switch (s) {
    case Status::UP: return "up";
    case Status::DOWN: return "down";
    case Status::PENDING: return "pending";
    case Status::MAINTENANCE: return "in_maintenance";
    default: return "unknown";
  }
}

static constexpr int MONITORS = 10;
// The ids of the tile's options of kind entity, in the order the inspector shows them.
static const char *const SLOTS[MONITORS] = {"monitor_1", "monitor_2", "monitor_3", "monitor_4", "monitor_5",
                                            "monitor_6", "monitor_7", "monitor_8", "monitor_9", "monitor_10"};

// The tile: a status page as Uptime Kuma draws one. A banner with the state of the whole page (a dot in its tone, the
// words, how many are up), and under it a row per monitor: its dot, its name and its state, as many as fit, problems
// first when the person keeps that option on. On a single cell it is a summary: how many are up, and the worst news.
// Home Assistant sends the chosen entities again whenever one of them changes; nothing on the screen polls.
class StatusTile : public tessera::Tile {
 public:
  explicit StatusTile(const tessera::Plugin *plugin) : plugin_(plugin) {}

  void create(const tessera::TileContext &c) override {
    width_ = c.width;
    height_ = c.height;
    compact_ = c.columns == 1 && c.rows == 1;
    narrow_ = c.columns == 1;
    problems_first_ = c.options["problems_first"] | true;

    // The monitors the person chose, in the inspector's order; a slot left empty is skipped.
    for (int i = 0; i < MONITORS; ++i) {
      const char *entity = c.options[SLOTS[i]] | "";
      if (!*entity) continue;
      Monitor &m = monitors_[count_++];
      m.slot = static_cast<uint8_t>(i);
      // Until Home Assistant sends it: the entity's own id, without its domain, as its name.
      const char *dot = strchr(entity, '.');
      copy(m.name, sizeof m.name, dot ? dot + 1 : entity);
    }
    for (int i = 0; i < count_; ++i) order_[i] = static_cast<uint8_t>(i);

    const char *title = c.options["title"] | "";
    if (!*title) title = c.name;
    if (!*title) title = plugin_->text("default_title");
    copy(title_text_, sizeof title_text_, title);

    pad_ = ui::px(ui::large() ? 8 : 5);
    dot_ = ui::px(ui::large() ? 10 : 8);
    if (compact_) make_compact(c.parent);
    else make_page(c.parent);
  }

  void on_state(JsonObjectConst data) override {
    too_large_ = !strcmp(data["wait"] | "", "too_large");
    JsonObjectConst entities = data["entities"];
    const char *suffix = plugin_->text("name_suffix");
    const size_t suffix_len = strlen(suffix);
    for (int i = 0; i < count_; ++i) {
      Monitor &m = monitors_[i];
      JsonObjectConst e = entities[SLOTS[m.slot]];
      if (e.isNull()) {
        m.status = Status::NO_DATA;
        continue;
      }
      m.status = status_of(e["state"] | "");
      const char *name = e["name"] | "";
      if (*name) {
        copy(m.name, sizeof m.name, name);
        // "Internet Status" is the integration's name for the sensor; the monitor is "Internet".
        const size_t n = strlen(m.name);
        if (suffix_len && n > suffix_len && !strcmp(m.name + n - suffix_len, suffix)) m.name[n - suffix_len] = '\0';
      }
    }
    // The order the rows show: as chosen, or problems first (and otherwise as chosen).
    for (int i = 0; i < count_; ++i) order_[i] = static_cast<uint8_t>(i);
    if (problems_first_)
      std::stable_sort(order_, order_ + count_, [this](uint8_t a, uint8_t b) {
        return static_cast<int>(monitors_[a].status) < static_cast<int>(monitors_[b].status);
      });
  }

  void on_tick(uint32_t) override {
    Summary s = summarise();
    if (compact_) paint_compact(s);
    else paint_page(s);
  }

  // Light and dark: every colour again. The painting calls change a colour only when its tone or role differs, so here
  // the styles are set outright from the tones the tile shows now.
  void on_theme() override {
    const Summary s = summarise();
    if (compact_) {
      lv_obj_set_style_text_color(title_, ui::color(theme::MUTED), 0);
      lv_obj_set_style_text_color(big_, ui::tone_color(s.tone, theme::INK), 0);
      lv_obj_set_style_text_color(phrase_, ui::tone_color(s.tone, theme::MUTED), 0);
      return;
    }
    if (title_) lv_obj_set_style_text_color(title_, ui::color(theme::MUTED), 0);
    lv_obj_set_style_bg_color(banner_, ui::color(theme::TRACK), 0);
    lv_obj_set_style_bg_color(banner_dot_, ui::tone_color(s.tone, theme::OFF), 0);
    lv_obj_set_style_text_color(phrase_, ui::color(theme::INK), 0);
    lv_obj_set_style_text_color(count_label_, ui::color(theme::MUTED), 0);
    lv_obj_set_style_text_color(more_, ui::color(theme::SUBTLE), 0);
    for (int r = 0; r < rows_; ++r) {
      const Row &row = row_[r];
      const Status st = row.shown < count_ ? monitors_[row.shown].status : Status::NO_DATA;
      lv_obj_set_style_bg_color(row.dot, ui::tone_color(tone_of(st), theme::OFF), 0);
      lv_obj_set_style_text_color(row.name, ui::color(theme::INK), 0);
      if (row.state)
        lv_obj_set_style_text_color(row.state, ui::tone_color(st == Status::UP ? Tone::NORMAL : tone_of(st), theme::MUTED), 0);
    }
  }

 private:
  struct Monitor {
    char name[49] = "";
    uint8_t slot = 0;
    Status status = Status::NO_DATA;
  };
  struct Row {
    lv_obj_t *dot{}, *name{}, *state{};
    uint8_t shown = 0xFF;   // the monitor it shows, 0xFF for none
  };
  struct Summary {
    int up = 0, down = 0, pending = 0, maintenance = 0, no_data = 0;
    Tone tone = Tone::NORMAL;
    const char *phrase = "choose";       // a key of the screen's texts, for the banner
    const char *short_phrase = "choose"; // the same for a single cell
    long n = 0;                          // the number a short phrase counts
  };

  static void copy(char *to, size_t size, const char *from) {
    strncpy(to, from, size - 1);
    to[size - 1] = '\0';
  }

  // The state of the whole page, the way Uptime Kuma's own status page words it: an outage outweighs a monitor that
  // is pending, which outweighs one in maintenance.
  Summary summarise() const {
    Summary s;
    for (int i = 0; i < count_; ++i) {
      switch (monitors_[i].status) {
        case Status::UP: ++s.up; break;
        case Status::DOWN: ++s.down; break;
        case Status::PENDING: ++s.pending; break;
        case Status::MAINTENANCE: ++s.maintenance; break;
        default: ++s.no_data; break;
      }
    }
    const int known = count_ - s.no_data;
    if (!count_) return s;
    if (too_large_ || !known) {
      s.phrase = too_large_ ? "too_large" : "no_data";
      s.short_phrase = too_large_ ? "too_large" : "short_no_data";
    } else if (s.down) {
      s.tone = Tone::ALERT;
      s.phrase = s.down == known ? "major_outage" : "partial_outage";
      s.short_phrase = "short_down";
      s.n = s.down;
    } else if (s.pending) {
      s.tone = Tone::BUSY;
      s.phrase = "degraded";
      s.short_phrase = "short_pending";
      s.n = s.pending;
    } else if (s.maintenance) {
      s.tone = Tone::ACCENT;
      s.phrase = "maintenance";
      s.short_phrase = "short_maintenance";
    } else {
      s.tone = Tone::GOOD;
      s.phrase = "operational";
      s.short_phrase = "short_operational";
    }
    return s;
  }

  std::string up_of_total(const Summary &s) const {
    char text[12];
    snprintf(text, sizeof text, "%d/%d", s.up, count_);
    return text;
  }

  // ---- A single cell: the title, how many are up, large, and the worst news under it.
  void make_compact(lv_obj_t *parent) {
    const int body = ui::line_height(Font::BODY);
    title_ = ui::label(parent, Font::BODY, theme::MUTED);
    lv_obj_set_size(title_, width_, body);
    lv_obj_set_pos(title_, 0, 0);
    big_ = ui::label(parent, Font::VALUE, theme::INK);
    lv_obj_set_width(big_, width_);
    phrase_ = ui::label(parent, Font::BODY, theme::MUTED);
    lv_obj_set_size(phrase_, width_, body);
    lv_obj_set_pos(phrase_, 0, height_ - body);
  }

  void paint_compact(const Summary &s) {
    ui::set_text(title_, title_text_);
    const int body = ui::line_height(Font::BODY);
    const std::string big = count_ ? up_of_total(s) : std::string();
    // The largest face the number fits in, between the title and the phrase. "10/10" is its widest shape.
    const int room = height_ - 2 * body;
    Font face = Font::BODY_LARGE;
    for (Font f : {Font::VALUE, Font::HEADLINE, Font::TITLE})
      if (ui::text_width("10/10", f) <= width_ && ui::line_height(f) <= room) {
        face = f;
        break;
      }
    ui::set_font(big_, face);
    ui::set_tone(big_, s.tone, theme::INK);
    ui::set_text(big_, big);
    const int h = ui::line_height(face);
    lv_obj_set_height(big_, h);
    lv_obj_set_pos(big_, 0, body + std::max(0, (room - h) / 2));
    ui::set_tone(phrase_, s.tone, theme::MUTED);
    ui::set_text(phrase_, s.n ? tessera::format(plugin_->text(s.short_phrase), s.n) : plugin_->text(s.short_phrase));
  }

  // ---- Two cells or more: the banner and a row per monitor.
  void make_page(lv_obj_t *parent) {
    const int body = ui::line_height(Font::BODY), title = ui::line_height(Font::TITLE);
    const int banner_h = title + 2 * ui::px(ui::large() ? 6 : 4);
    const int gap = ui::px(ui::large() ? 8 : 5);
    row_h_ = std::max(body, dot_);
    row_step_ = row_h_ + ui::px(ui::large() ? 6 : 3);
    auto rows_below = [&](int top) { return std::max(0, (height_ - top + (row_step_ - row_h_)) / row_step_); };
    // The title above the banner only when it leaves room for three rows (or every monitor): on a short tile the
    // banner and the monitors matter more than a name.
    int y = 0;
    const int with_title = rows_below(body + ui::px(2) + banner_h + gap);
    if (with_title >= std::min(count_, 3) && with_title > 0) {
      title_ = ui::label(parent, Font::BODY, theme::MUTED);
      lv_obj_set_size(title_, width_, body);
      lv_obj_set_pos(title_, 0, 0);
      y = body + ui::px(2);
    }

    banner_ = ui::block(parent, theme::TRACK);
    lv_obj_set_size(banner_, width_, banner_h);
    lv_obj_set_pos(banner_, 0, y);
    banner_dot_ = ui::block(banner_, theme::OFF);
    lv_obj_set_size(banner_dot_, dot_, dot_);
    lv_obj_set_style_radius(banner_dot_, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_pos(banner_dot_, pad_, (banner_h - dot_) / 2);
    // The count of monitors that are up, at the banner's right; the words take the rest.
    count_w_ = ui::text_width("10/10", Font::BODY) + ui::px(2);
    count_label_ = ui::label(banner_, Font::BODY, theme::MUTED);
    lv_obj_set_size(count_label_, count_w_, body);
    lv_obj_set_style_text_align(count_label_, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_pos(count_label_, width_ - pad_ - count_w_, (banner_h - body) / 2);
    phrase_ = ui::label(banner_, Font::TITLE, theme::INK);
    const int phrase_x = pad_ + dot_ + ui::px(6);
    phrase_w_ = std::max(1, width_ - phrase_x - count_w_ - 2 * pad_);
    lv_obj_set_size(phrase_, phrase_w_, title);
    lv_obj_set_pos(phrase_, phrase_x, (banner_h - title) / 2);
    y += banner_h + gap;

    rows_ = std::min(count_, std::min(MONITORS, rows_below(y)));
    const int state_w = narrow_ ? 0 : ui::text_width(plugin_->text("in_maintenance"), Font::BODY) + ui::px(2);
    for (int r = 0; r < rows_; ++r) {
      Row &row = row_[r];
      const int top = y + r * row_step_;
      row.dot = ui::block(parent, theme::OFF);
      lv_obj_set_size(row.dot, dot_, dot_);
      lv_obj_set_style_radius(row.dot, LV_RADIUS_CIRCLE, 0);
      lv_obj_set_pos(row.dot, pad_ / 2, top + (row_h_ - dot_) / 2);
      const int name_x = pad_ / 2 + dot_ + ui::px(6);
      row.name = ui::label(parent, Font::BODY, theme::INK);
      lv_obj_set_size(row.name, std::max(1, width_ - name_x - (state_w ? state_w + ui::px(6) : 0)), body);
      lv_obj_set_pos(row.name, name_x, top + (row_h_ - body) / 2);
      if (state_w) {
        row.state = ui::label(parent, Font::BODY, theme::MUTED);
        lv_obj_set_size(row.state, state_w, body);
        lv_obj_set_style_text_align(row.state, LV_TEXT_ALIGN_RIGHT, 0);
        lv_obj_set_pos(row.state, width_ - state_w, top + (row_h_ - body) / 2);
      }
    }
    // When not every monitor fits, the last row says how many more there are instead of showing one.
    more_ = ui::label(parent, Font::BODY, theme::SUBTLE);
    lv_obj_set_size(more_, width_, body);
    lv_obj_set_pos(more_, pad_ / 2, rows_ ? y + (rows_ - 1) * row_step_ + (row_h_ - body) / 2 : height_ - body);
  }

  void paint_page(const Summary &s) {
    if (title_) ui::set_text(title_, title_text_);
    ui::set_fill(banner_dot_, s.tone, theme::OFF);
    // The banner's words, or the short ones of a single cell where they would be cut ("2 down" for "Partial outage").
    std::string words = plugin_->text(s.phrase);
    if (ui::text_width(words, Font::TITLE) > phrase_w_)
      words = s.n ? tessera::format(plugin_->text(s.short_phrase), s.n) : std::string(plugin_->text(s.short_phrase));
    ui::set_text(phrase_, words);
    ui::set_text(count_label_, count_ ? up_of_total(s) : std::string());

    // How many rows show a monitor: all of them when they fit, else one less and a line that counts the rest.
    const bool overflow = count_ > rows_ && rows_ > 0;
    const int shown = overflow ? rows_ - 1 : rows_;
    for (int r = 0; r < rows_; ++r) {
      Row &row = row_[r];
      const bool used = r < shown;
      row.shown = used ? order_[r] : 0xFF;
      lv_obj_set_style_opa(row.dot, used ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
      if (!used) {
        ui::set_text(row.name, "");
        if (row.state) ui::set_text(row.state, "");
        continue;
      }
      const Monitor &m = monitors_[row.shown];
      ui::set_fill(row.dot, tone_of(m.status), theme::OFF);
      ui::set_text(row.name, m.name);
      if (row.state) {
        ui::set_tone(row.state, m.status == Status::UP ? Tone::NORMAL : tone_of(m.status), theme::MUTED);
        ui::set_text(row.state, plugin_->text(word_of(m.status)));
      }
    }
    ui::set_text(more_, overflow ? tessera::format(plugin_->text("more"), count_ - shown) : std::string());
  }

  const tessera::Plugin *plugin_;
  int width_ = 0, height_ = 0, pad_ = 0, dot_ = 0, row_h_ = 0, row_step_ = 0, count_w_ = 0, phrase_w_ = 0;
  bool compact_ = false, narrow_ = false, problems_first_ = true, too_large_ = false;
  char title_text_[65] = "";
  Monitor monitors_[MONITORS];
  uint8_t order_[MONITORS] = {};
  int count_ = 0;
  Row row_[MONITORS];
  int rows_ = 0;
  lv_obj_t *title_{}, *big_{}, *phrase_{}, *banner_{}, *banner_dot_{}, *count_label_{}, *more_{};
};

void UptimeKumaStatus::setup() {
  add_tile("status_page", [this]() { return new StatusTile(this); });
}

}  // namespace esphome::uptime_kuma_status
