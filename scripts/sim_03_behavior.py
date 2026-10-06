"""
scripts/sim_03_behavior.py

Stage 3 — the behavior sim. For every subscription period, iterate day-by-day
generating sessions and per-episode watch events.

Per user:
  - Precompute static score vector over all titles (NumPy)
  - For each day in each period:
      - Roll activity (weekday × life_context × seasonal × honeymoon)
      - If active: 1-3 sessions
      - Per session: pick session type → intensity → continue-vs-new → pick title
      - Watch N events (episode/movie)
      - Track completion, abandonment, series progress

Full NumPy scoring on every candidate at every session. No affinity pre-filter.

Deterministic via RANDOM_SEED. Wipes prior behavior tables on rerun.

Run:
    python -m scripts.sim_03_behavior
"""

from __future__ import annotations

import random
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import DATABASE_URL
from app.models.behavior import (
    ContentTypeWatched,
    Session as WatchSession,
    SeriesProgressState,
    UserSeriesProgress,
    WatchEvent,
)
from app.models.subscription import SubscriptionPeriod
from app.models.title import Base, ContentType, Title
from app.models.user import Archetype, User
from app.sim.behavior_rules import (
    classify_discovery,
    daily_activity_multiplier,
    n_sessions_today,
    prob_continue_in_progress,
    pick_session_type,
    sample_completion_pct,
    sample_session_intensity,
)
from app.sim.lifecycle_rules import SIM_END, SIM_START
from config.vocabularies import GENRES, THEMES, TONES


RANDOM_SEED = 42

# --- Behavior tuning constants ---
CROSS_BORDER_EXPONENT = 3.0       # fame^3 penalty for foreign titles
DUBBED_FAME_THRESHOLD = 0.70      # fame above this = assumed dubbed
NEW_RELEASE_WINDOW_DAYS = 30      # titles this fresh get big novelty bonus
FAME_MAINSTREAM_BOOST = 0.5       # base × (1 + fame × mainstream_susc × this)
ABANDONMENT_TIMEOUT_DAYS = 90     # no return in this many days -> auto-abandon
MATURITY_INDEX = {"7+": 0, "12+": 1, "16+": 2, "18+": 3}

BATCH_USERS_PER_COMMIT = 50


# ===========================================================================
# Title matrix precomputation
# ===========================================================================
def build_title_matrices(titles: list[Title]) -> dict:
    """
    Return a dict of numpy arrays and metadata, indexed 0..N-1 over titles.
    """
    n = len(titles)
    title_ids = np.array([str(t.title_id) for t in titles], dtype=object)
    fame = np.array([float(t.fame or 0.0) for t in titles], dtype=np.float32)
    add_dates = np.array(
        [(t.platform_add_date - SIM_START).days if t.platform_add_date else -1 for t in titles],
        dtype=np.int32,
    )
    leaving_dates = np.array(
        [(t.platform_leaving_date - SIM_START).days if t.platform_leaving_date else 99999 for t in titles],
        dtype=np.int32,
    )
    is_movie = np.array(
        [t.content_type == ContentType.MOVIE for t in titles], dtype=bool
    )
    is_series = ~is_movie
    countries = np.array([t.country or "US" for t in titles], dtype=object)
    langs = np.array([t.original_language or "en" for t in titles], dtype=object)
    maturity_idx = np.array(
        [MATURITY_INDEX.get(t.maturity_rating.value, 3) for t in titles],
        dtype=np.int8,
    )
    binge_factors = np.array(
        [float(t.binge_factor or 0.5) for t in titles], dtype=np.float32
    )
    runtime_minutes = np.array(
        [int(t.runtime_minutes or 100) for t in titles], dtype=np.int32
    )
    episode_count = np.array(
        [int(t.episode_count or 0) for t in titles], dtype=np.int32
    )
    season_count = np.array(
        [int(t.season_count or 1) for t in titles], dtype=np.int32
    )
    avg_ep_length = np.array(
        [int(t.avg_episode_length or 45) for t in titles], dtype=np.int32
    )

    # binary presence matrices for genres/themes/tones
    genre_idx = {g: i for i, g in enumerate(GENRES)}
    theme_idx = {t: i for i, t in enumerate(THEMES)}
    tone_idx = {tn: i for i, tn in enumerate(TONES)}

    G = np.zeros((n, len(GENRES)), dtype=np.float32)
    T = np.zeros((n, len(THEMES)), dtype=np.float32)
    Tn = np.zeros((n, len(TONES)), dtype=np.float32)

    n_g = np.zeros(n, dtype=np.float32)
    n_t = np.zeros(n, dtype=np.float32)
    n_tn = np.zeros(n, dtype=np.float32)

    for i, t in enumerate(titles):
        for g in (t.genres or []):
            if g in genre_idx:
                G[i, genre_idx[g]] = 1.0
                n_g[i] += 1
        for th in (t.themes or []):
            if th in theme_idx:
                T[i, theme_idx[th]] = 1.0
                n_t[i] += 1
        for tn in (t.tones or []):
            if tn in tone_idx:
                Tn[i, tone_idx[tn]] = 1.0
                n_tn[i] += 1

    # avoid divide-by-zero for titles missing tags
    n_g[n_g == 0] = 1
    n_t[n_t == 0] = 1
    n_tn[n_tn == 0] = 1

    return {
        "n": n,
        "title_ids": title_ids,
        "title_objs": titles,  # keep original objs for episode counts etc.
        "fame": fame,
        "add_dates": add_dates,
        "leaving_dates": leaving_dates,
        "is_movie": is_movie,
        "is_series": is_series,
        "countries": countries,
        "langs": langs,
        "maturity_idx": maturity_idx,
        "binge_factors": binge_factors,
        "runtime_minutes": runtime_minutes,
        "episode_count": episode_count,
        "season_count": season_count,
        "avg_ep_length": avg_ep_length,
        "G": G, "T": T, "Tn": Tn,
        "n_g": n_g, "n_t": n_t, "n_tn": n_tn,
    }


def user_country_language(country: str) -> str:
    """Rough country->language mapping for subtitle-match logic."""
    return {
        "US": "en", "GB": "en", "IN": "hi", "KR": "ko", "JP": "ja",
        "FR": "fr", "ES": "es", "DE": "de", "MX": "es", "BR": "pt",
    }.get(country, "en")


def precompute_user_static_score(user: User, tm: dict) -> np.ndarray:
    """
    Static per-user score over all titles. Combines:
      - affinity_match (genre + theme + tone)
      - cross-border penalty (fame^3 for foreign titles)
      - subtitle-access (fame>0.7 assumed dubbed, else user tolerance)
      - maturity cap
    Returns float32[n_titles].
    """
    n = tm["n"]

    # Build user's affinity vectors as numpy
    g_vec = np.array([user.genre_affinity.get(g, 0.30) for g in GENRES], dtype=np.float32)
    t_vec = np.array([user.theme_affinity.get(t, 0.30) for t in THEMES], dtype=np.float32)
    tn_vec = np.array([user.tone_affinity.get(tn, 0.30) for tn in TONES], dtype=np.float32)

    # Vectorized affinity match per title
    genre_score = (tm["G"] @ g_vec) / tm["n_g"]
    theme_score = (tm["T"] @ t_vec) / tm["n_t"]
    tone_score = (tm["Tn"] @ tn_vec) / tm["n_tn"]
    affinity = 0.4 * genre_score + 0.3 * theme_score + 0.3 * tone_score

    # Cross-border: penalty = fame^3 for foreign (same country = 1.0)
    same_country = (tm["countries"] == user.country).astype(np.float32)
    cross_factor = same_country + (1 - same_country) * (tm["fame"] ** CROSS_BORDER_EXPONENT)

    # Subtitle / language access
    user_lang = user_country_language(user.country)
    same_lang = (tm["langs"] == user_lang).astype(np.float32)
    dubbed = (tm["fame"] >= DUBBED_FAME_THRESHOLD).astype(np.float32)
    subtitle_access = np.maximum(
        same_lang,
        np.maximum(dubbed, np.full(n, user.subtitle_tolerance, dtype=np.float32))
    )

    # Maturity cap
    user_ceiling_idx = MATURITY_INDEX.get(user.maturity_ceiling, 3)
    maturity_ok = (tm["maturity_idx"] <= user_ceiling_idx).astype(np.float32)

    # Combined static base
    base = affinity * cross_factor * subtitle_access * maturity_ok

    # Fame bump by mainstream susceptibility
    base = base * (1.0 + tm["fame"] * user.mainstream_susceptibility * FAME_MAINSTREAM_BOOST)

    return base.astype(np.float32)


# ===========================================================================
# Main per-user sim
# ===========================================================================
def simulate_user(
    user: User,
    periods: list[SubscriptionPeriod],
    tm: dict,
    rng: random.Random,
    np_rng: np.random.Generator,
):
    """
    Return (sessions, events, progress_rows) for this user.
    """
    sessions: list[WatchSession] = []
    events: list[WatchEvent] = []
    progress_map: dict[str, UserSeriesProgress] = {}

    n = tm["n"]
    base_score = precompute_user_static_score(user, tm)

    # State masks
    completed_mask = np.zeros(n, dtype=bool)
    abandoned_mask = np.zeros(n, dtype=bool)
    already_touched_mask = np.zeros(n, dtype=bool)

    # In-progress series: title_idx -> {season, ep, started_at, last_watched, total_eps}
    in_progress: dict[int, dict] = {}

    weekday_activity = np.array(user.weekday_activity)
    hour_profile = np.array(user.hour_of_day_profile)
    hour_profile = hour_profile / hour_profile.sum() if hour_profile.sum() > 0 else np.full(24, 1/24)

    for period in periods:
        p_start = period.started_on
        p_end = period.ended_on or SIM_END
        day = p_start

        while day <= p_end:
            # Passive abandonment: any in_progress with no watch in ABANDONMENT_TIMEOUT_DAYS
            stale = [
                idx for idx, st in in_progress.items()
                if (day - st["last_watched"]).days > ABANDONMENT_TIMEOUT_DAYS
            ]
            for idx in stale:
                st = in_progress.pop(idx)
                title_id_str = str(tm["title_ids"][idx])
                if title_id_str in progress_map:
                    p = progress_map[title_id_str]
                    p.state = SeriesProgressState.ABANDONED
                    p.ended_at = day
                    p.abandonment_episode = st["ep"] + (st["season"] - 1) * 10
                abandoned_mask[idx] = True

            days_in_period = (day - p_start).days
            dow = day.weekday()
            base_p = weekday_activity[dow]
            life_mult = daily_activity_multiplier(
                user.life_context_pattern.value, user.country, day, days_in_period,
            )
            activity_prob = base_p * life_mult

            if rng.random() > min(activity_prob, 0.95):
                day += timedelta(days=1)
                continue

            n_sess = n_sessions_today(activity_prob, rng)
            for _ in range(n_sess):
                hour = int(np_rng.choice(24, p=hour_profile))
                s_type = pick_session_type(user.archetype, rng)
                is_weekend = dow >= 5
                intensity = sample_session_intensity(
                    s_type, user.session_intensity_mean, user.session_intensity_std,
                    is_weekend, rng,
                )

                session_start = datetime.combine(day, datetime.min.time()).replace(
                    hour=hour, minute=rng.randint(0, 59)
                )
                session_events: list[WatchEvent] = []
                cursor_time = session_start

                # Decide session shape: rewatch, continue, or new
                is_rewatch_session = (
                    s_type == SessionType_REWATCH() and completed_mask.any()
                    and rng.random() < user.rewatch_tendency
                )
                did_continue = False

                # Enforce concurrent_capacity: at capacity, boost continue prob
                at_capacity = len(in_progress) >= user.concurrent_capacity

                # Try to continue in-progress
                p_continue = prob_continue_in_progress(s_type, len(in_progress))
                if at_capacity and in_progress and not is_rewatch_session:
                    p_continue = max(p_continue, 0.90)

                if in_progress and rng.random() < p_continue and not is_rewatch_session:
                    # Pick in-progress by binge_factor weight
                    idxs = list(in_progress.keys())
                    weights = np.array([tm["binge_factors"][i] for i in idxs])
                    weights = weights / weights.sum()
                    pick_idx = int(np_rng.choice(len(idxs), p=weights))
                    cont_idx = idxs[pick_idx]

                    # Checkpoint-based abandonment: roll only when crossing a
                    # decision point since last check (1, 3, 8, 15, 25 eps).
                    # Softened: threshold = 0.5 + completion_long * 0.5
                    st = in_progress[cont_idx]
                    eps_now = st["total_eps"]
                    last_check = st.get("last_abandon_check_ep", 0)
                    checkpoints = (1, 3, 8, 15, 25)
                    crossed = any(last_check < cp <= eps_now for cp in checkpoints)

                    should_abandon = False
                    if crossed:
                        st["last_abandon_check_ep"] = eps_now
                        abandon_threshold = 0.5 + user.completion_long * 0.5
                        if rng.random() > abandon_threshold:
                            should_abandon = True

                    if should_abandon:
                        st_popped = in_progress.pop(cont_idx)
                        title_id_str = str(tm["title_ids"][cont_idx])
                        if title_id_str in progress_map:
                            p = progress_map[title_id_str]
                            p.state = SeriesProgressState.ABANDONED
                            p.ended_at = day
                            p.abandonment_episode = st_popped["ep"] + (st_popped["season"] - 1) * 10
                        abandoned_mask[cont_idx] = True
                    else:
                        did_continue = True
                        _watch_series_episodes(
                            user, cont_idx, in_progress, tm, intensity, hour,
                            cursor_time, session_events, progress_map,
                            completed_mask, is_continue=True, rng=rng,
                        )

                if not did_continue:
                    # New pick — rewatch or fresh
                    if is_rewatch_session and completed_mask.any():
                        candidates = np.where(completed_mask)[0]
                        picked = _pick_from_candidates(
                            candidates, base_score, tm, day, np_rng, user, rng
                        )
                    else:
                        # Available today + not abandoned + not completed
                        day_offset = (day - SIM_START).days
                        avail = (tm["add_dates"] >= 0) & (tm["add_dates"] <= day_offset) & \
                                (tm["leaving_dates"] > day_offset)
                        candidate_mask = avail & ~completed_mask & ~abandoned_mask
                        # Bias toward movie vs series by user's ratio.
                        # If at capacity, FORCE movie — can't take on another series.
                        want_movie = rng.random() < user.movie_series_ratio
                        if at_capacity:
                            want_movie = True
                        if want_movie:
                            candidate_mask = candidate_mask & tm["is_movie"]
                        else:
                            candidate_mask = candidate_mask & tm["is_series"]
                        candidates = np.where(candidate_mask)[0]
                        if len(candidates) == 0:
                            if at_capacity:
                                # don't fall back to series when at capacity
                                continue
                            # fallback: any available
                            candidates = np.where(avail & ~completed_mask & ~abandoned_mask)[0]
                        if len(candidates) == 0:
                            continue
                        picked = _pick_from_candidates(
                            candidates, base_score, tm, day, np_rng, user, rng,
                        )
                    if picked is None:
                        continue
                    already_touched_mask[picked] = True

                    if tm["is_movie"][picked]:
                        _watch_movie(
                            user, picked, tm, hour, cursor_time,
                            session_events, completed_mask, abandoned_mask,
                            is_rewatch_session, rng,
                        )
                    else:
                        # Start new series from S1E1
                        title_id_str = str(tm["title_ids"][picked])
                        in_progress[picked] = {
                            "season": 1, "ep": 1,
                            "started_at": day, "last_watched": day,
                            "total_eps": 0,
                            "last_abandon_check_ep": 0,
                        }
                        progress_map[title_id_str] = UserSeriesProgress(
                            user_id=user.user_id,
                            title_id=uuid.UUID(title_id_str),
                            state=SeriesProgressState.IN_PROGRESS,
                            current_season=1, current_episode=1,
                            started_at=day, last_watched_at=day,
                            total_episodes_watched=0,
                        )
                        _watch_series_episodes(
                            user, picked, in_progress, tm, intensity, hour,
                            cursor_time, session_events, progress_map,
                            completed_mask, is_continue=False, rng=rng,
                        )

                if not session_events:
                    continue

                # Wrap up session
                session_end = session_events[-1].ended_at
                total_min = sum(e.minutes_watched for e in session_events)
                s_obj = WatchSession(
                    session_id=uuid.uuid4(),
                    user_id=user.user_id,
                    started_at=session_start,
                    ended_at=session_end,
                    day_of_week=dow,
                    hour_started=hour,
                    session_type=s_type,
                    event_count=len(session_events),
                    total_minutes=total_min,
                )
                sessions.append(s_obj)
                # Assign session_id to events
                for e in session_events:
                    e.session_id = s_obj.session_id
                events.extend(session_events)

            day += timedelta(days=1)

        # End of period — freeze in_progress as PAUSED (they'll resume next period if applicable)
        for idx, st in list(in_progress.items()):
            title_id_str = str(tm["title_ids"][idx])
            if title_id_str in progress_map:
                p = progress_map[title_id_str]
                if p.state == SeriesProgressState.IN_PROGRESS:
                    p.state = SeriesProgressState.PAUSED
                    p.ended_at = p_end

    return sessions, events, list(progress_map.values())


# --- session-type constant helper (avoids circular import at method-def time) ---
def SessionType_REWATCH():
    from app.models.behavior import SessionType
    return SessionType.REWATCH


# ===========================================================================
# Watch helpers
# ===========================================================================
def _pick_from_candidates(
    candidates: np.ndarray, base_score: np.ndarray, tm: dict,
    day: date, np_rng: np.random.Generator, user: User, rng: random.Random,
) -> int | None:
    """Weighted sampling over a candidate index set."""
    if len(candidates) == 0:
        return None
    scores = base_score[candidates].copy()

    # Novelty bonus for recent adds
    day_offset = (day - SIM_START).days
    days_since_add = day_offset - tm["add_dates"][candidates]
    nov = np.where(
        days_since_add <= NEW_RELEASE_WINDOW_DAYS,
        1.0 + user.novelty_seeking * 0.5,
        np.where(
            days_since_add <= 90,
            1.0 + user.novelty_seeking * 0.25,
            np.where(days_since_add <= 365, 1.0 + user.novelty_seeking * 0.1, 1.0),
        ),
    ).astype(np.float32)
    scores = scores * nov

    # Mood variance jitter
    if user.mood_variance > 0:
        jitter = np_rng.uniform(
            1 - user.mood_variance, 1 + user.mood_variance, size=len(candidates),
        ).astype(np.float32)
        scores = scores * jitter

    scores = np.maximum(scores, 1e-6)
    total = scores.sum()
    if total <= 0:
        return int(candidates[0])
    probs = scores / total
    pick = int(np_rng.choice(len(candidates), p=probs))
    return int(candidates[pick])


def _watch_movie(
    user, title_idx, tm, hour, cursor_time, session_events,
    completed_mask, abandoned_mask, is_rewatch, rng,
):
    from app.models.behavior import WatchEvent, ContentTypeWatched
    days_since_add = (cursor_time.date() - SIM_START).days - tm["add_dates"][title_idx]
    completion = sample_completion_pct(
        user.completion_short, 1, hour, user.sleep_dropoff, rng,
    )
    runtime = int(tm["runtime_minutes"][title_idx])
    minutes = int(round(runtime * completion))
    end = cursor_time + timedelta(minutes=minutes)
    disc = classify_discovery(False, is_rewatch, days_since_add, float(tm["fame"][title_idx]))

    e = WatchEvent(
        user_id=user.user_id,
        title_id=uuid.UUID(str(tm["title_ids"][title_idx])),
        content_type=ContentTypeWatched.MOVIE,
        season_num=None, episode_num=None,
        started_at=cursor_time, ended_at=end,
        minutes_watched=minutes,
        completion_pct=completion,
        was_completed=completion >= 0.90,
        was_abandoned=completion < 0.20,
        discovery_source=disc,
    )
    session_events.append(e)

    if completion >= 0.90:
        completed_mask[title_idx] = True
    elif completion < 0.20:
        abandoned_mask[title_idx] = True


def _watch_series_episodes(
    user, title_idx, in_progress, tm, intensity, hour,
    cursor_time, session_events, progress_map, completed_mask,
    is_continue, rng,
):
    from app.models.behavior import WatchEvent, ContentTypeWatched
    st = in_progress[title_idx]
    ep_len = int(tm["avg_ep_length"][title_idx])
    days_since_add = (cursor_time.date() - SIM_START).days - tm["add_dates"][title_idx]
    fame = float(tm["fame"][title_idx])
    disc = classify_discovery(is_continue, False, days_since_add, fame)

    ep_count_total = int(tm["episode_count"][title_idx])
    season_count = int(tm["season_count"][title_idx])
    eps_per_season = max(1, ep_count_total // max(1, season_count))

    now = cursor_time
    watched_this_session = 0
    for i in range(intensity):
        if st["total_eps"] >= ep_count_total:
            # Finished series
            title_id_str = str(tm["title_ids"][title_idx])
            if title_id_str in progress_map:
                p = progress_map[title_id_str]
                p.state = SeriesProgressState.COMPLETED
                p.ended_at = cursor_time.date()
            completed_mask[title_idx] = True
            in_progress.pop(title_idx, None)
            break

        completion = sample_completion_pct(
            0.85, i + 1, hour, user.sleep_dropoff, rng,
        )
        minutes = int(round(ep_len * completion))
        end = now + timedelta(minutes=minutes)

        e = WatchEvent(
            user_id=user.user_id,
            title_id=uuid.UUID(str(tm["title_ids"][title_idx])),
            content_type=ContentTypeWatched.EPISODE,
            season_num=st["season"], episode_num=st["ep"],
            started_at=now, ended_at=end,
            minutes_watched=minutes,
            completion_pct=completion,
            was_completed=completion >= 0.90,
            was_abandoned=completion < 0.20,
            discovery_source=disc,
        )
        session_events.append(e)
        watched_this_session += 1

        # Advance state
        if completion >= 0.20:
            st["total_eps"] += 1
            st["ep"] += 1
            if st["ep"] > eps_per_season:
                st["ep"] = 1
                st["season"] += 1
                if st["season"] > season_count:
                    # Finished
                    title_id_str = str(tm["title_ids"][title_idx])
                    if title_id_str in progress_map:
                        p = progress_map[title_id_str]
                        p.state = SeriesProgressState.COMPLETED
                        p.ended_at = end.date()
                        p.total_episodes_watched = st["total_eps"]
                    completed_mask[title_idx] = True
                    in_progress.pop(title_idx, None)
                    break

        now = end
        # If sleep dropoff early-terminates a session
        if user.sleep_dropoff and completion < 0.30 and i > 0:
            break

    # Update progress record
    st_still = in_progress.get(title_idx)
    if st_still is not None:
        title_id_str = str(tm["title_ids"][title_idx])
        if title_id_str in progress_map:
            p = progress_map[title_id_str]
            p.current_season = st_still["season"]
            p.current_episode = st_still["ep"]
            p.last_watched_at = now.date()
            p.total_episodes_watched = st_still["total_eps"]
        st_still["last_watched"] = now.date()


# ===========================================================================
# Main
# ===========================================================================
def main():
    started = time.time()
    rng = random.Random(RANDOM_SEED)
    np_rng = np.random.default_rng(RANDOM_SEED)

    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        # Wipe prior behavior tables
        print("Wiping prior behavior tables...")
        session.execute(WatchEvent.__table__.delete())
        session.execute(WatchSession.__table__.delete())
        session.execute(UserSeriesProgress.__table__.delete())
        session.commit()

        print("Loading catalog + users + periods...")
        titles = session.scalars(select(Title)).all()
        users = session.scalars(select(User)).all()
        periods_all = session.scalars(select(SubscriptionPeriod).order_by(SubscriptionPeriod.user_id, SubscriptionPeriod.period_num)).all()

        # Group periods by user
        periods_by_user: dict[uuid.UUID, list[SubscriptionPeriod]] = defaultdict(list)
        for p in periods_all:
            periods_by_user[p.user_id].append(p)

        print(f"  {len(titles)} titles, {len(users)} users, {len(periods_all)} periods")

        print("\nBuilding title matrices (NumPy)...")
        tm = build_title_matrices(titles)
        print(f"  matrices: G={tm['G'].shape}, T={tm['T'].shape}, Tn={tm['Tn'].shape}")

        print("\nRunning behavior sim...")
        total_sessions = 0
        total_events = 0
        total_progress = 0
        buffer_sessions: list[WatchSession] = []
        buffer_events: list[WatchEvent] = []
        buffer_progress: list[UserSeriesProgress] = []

        for i, user in enumerate(users, 1):
            u_periods = periods_by_user.get(user.user_id, [])
            if not u_periods:
                continue
            sess, evs, prog = simulate_user(user, u_periods, tm, rng, np_rng)
            buffer_sessions.extend(sess)
            buffer_events.extend(evs)
            buffer_progress.extend(prog)

            if i % BATCH_USERS_PER_COMMIT == 0:
                session.add_all(buffer_sessions)
                session.commit()
                session.add_all(buffer_events)
                session.commit()
                session.add_all(buffer_progress)
                session.commit()
                total_sessions += len(buffer_sessions)
                total_events += len(buffer_events)
                total_progress += len(buffer_progress)
                buffer_sessions, buffer_events, buffer_progress = [], [], []
                elapsed = time.time() - started
                rate = i / elapsed
                eta_min = (len(users) - i) / rate / 60
                print(f"  {i}/{len(users)} users  "
                      f"sessions={total_sessions:,} events={total_events:,} progress={total_progress:,}  "
                      f"elapsed={elapsed/60:.1f}min ETA={eta_min:.1f}min")

        # Final flush
        if buffer_sessions or buffer_events or buffer_progress:
            session.add_all(buffer_sessions)
            session.commit()
            session.add_all(buffer_events)
            session.commit()
            session.add_all(buffer_progress)
            session.commit()
            total_sessions += len(buffer_sessions)
            total_events += len(buffer_events)
            total_progress += len(buffer_progress)

    elapsed = time.time() - started
    print(f"\nDONE in {elapsed/60:.1f} min.")
    print(f"  sessions: {total_sessions:,}")
    print(f"  events:   {total_events:,}")
    print(f"  progress: {total_progress:,}")


if __name__ == "__main__":
    main()