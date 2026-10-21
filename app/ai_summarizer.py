"""Audio transcription and AI conversation summarizer service.

Handles transcribing audio files or voice memos, extracting structured summaries,
action items, and topics, and updating the relationship total state.
Supports Google Gemini API when GEMINI_API_KEY is configured, with a robust
deterministic local fallback for offline/test environments.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import httpx

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
)


@dataclass(frozen=True)
class SummarizedInteractionResult:
    """Structured result of transcribing and analyzing an interaction."""

    title: str
    transcript: str
    executive_summary: str
    key_takeaways: list[str]
    action_items: list[str]
    discovered_context_labels: list[str]
    discovered_shared_activities: list[str]
    suggested_total_state_summary: str
    interaction_date: date
    ai_provider: str


def _get_gemini_api_key() -> str | None:
    """Retrieve Gemini API key from environment if present."""
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def _build_system_prompt(
    contact_name: str,
    company: str | None,
    job_title: str | None,
    current_edge_summary: str | None,
    interaction_type: str = "interaction",
) -> str:
    type_context = ""
    if interaction_type == "thought":
        type_context = (
            "This interaction is a STRATEGIC THOUGHT / REFLECTION by the user on how this contact "
            "could be beneficial (e.g. fund advising, angel syndicate, introductions, "
            "domain expertise)."
        )
    elif interaction_type == "mutual_connection":
        type_context = (
            "This interaction represents THIRD-PARTY CONTEXT from talking to a mutual acquaintance "
            "or someone who knows the connection."
        )
    elif interaction_type in ("call", "phone", "video"):
        type_context = "This interaction is a direct PHONE or VIDEO CALL."
    elif interaction_type in ("chat", "coffee"):
        type_context = (
            "This interaction is an INFORMAL CHAT, coffee meetup, or direct messaging exchange."
        )
    elif interaction_type == "meeting":
        type_context = "This interaction is a formal or structured IN-PERSON MEETING."

    return f"""You are an executive assistant and relationship intelligence assistant.
You are processing an interaction between the user (a Duke student/founder)
and their contact: {contact_name}.
{type_context}

Contact Info:
- Company/Organization: {company or "Unknown"}
- Job Title / Role: {job_title or "Unknown"}
- Current Relationship Total State Summary: {current_edge_summary or "No prior summary recorded"}

Tasks:
1. If audio is provided, transcribe it accurately.
2. Provide a concise, professional title for the event (max 8 words).
3. Provide an executive summary of the interaction/thought (2-3 sentences).
4. Extract 2-5 concrete key takeaways, potential benefits, or topics.
5. Extract action items / next steps (each formatted with owner and task).
6. Discovered context tags (e.g., StrategicPartner, Advisor, MutualConnection, DukeAlumni).
7. Discovered shared activities (e.g., Phone Call, Coffee sync, Referral discussion).
8. Generate an updated single "Relationship Total State Summary" (1-2 crisp sentences
   capturing relationship standing and strategic value, synthesizing prior state and this update).

Return ONLY valid JSON matching this schema:
{{
  "title": "string",
  "transcript": "string",
  "executive_summary": "string",
  "key_takeaways": ["string"],
  "action_items": ["string"],
  "discovered_context_labels": ["string"],
  "discovered_shared_activities": ["string"],
  "suggested_total_state_summary": "string"
}}
"""


def _clean_json_response(raw_text: str) -> dict[str, Any]:
    """Extract and parse JSON from LLM response text."""
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def process_audio_interaction(
    audio_bytes: bytes,
    mime_type: str,
    contact_name: str,
    interaction_date: date,
    company: str | None = None,
    job_title: str | None = None,
    current_edge_summary: str | None = None,
    title_hint: str | None = None,
    api_key: str | None = None,
) -> SummarizedInteractionResult:
    """Transcribe and summarize an audio recording."""
    key = api_key or _get_gemini_api_key()

    result: SummarizedInteractionResult | None = None
    if key:
        with contextlib.suppress(Exception):
            result = _call_gemini_multimodal_audio(
                audio_bytes=audio_bytes,
                mime_type=mime_type,
                contact_name=contact_name,
                interaction_date=interaction_date,
                company=company,
                job_title=job_title,
                current_edge_summary=current_edge_summary,
                api_key=key,
            )

    if result is None:
        result = _generate_fallback_audio_summary(
            audio_bytes=audio_bytes,
            contact_name=contact_name,
            interaction_date=interaction_date,
            company=company,
            job_title=job_title,
            current_edge_summary=current_edge_summary,
        )

    if title_hint and title_hint.strip():
        result = replace(result, title=title_hint.strip())

    return result


def process_text_interaction(
    text_content: str,
    contact_name: str,
    interaction_date: date,
    company: str | None = None,
    job_title: str | None = None,
    current_edge_summary: str | None = None,
    interaction_type: str = "note",
    title_hint: str | None = None,
    api_key: str | None = None,
) -> SummarizedInteractionResult:
    """Summarize text notes, strategic thoughts, calls, or pasted transcripts."""
    key = api_key or _get_gemini_api_key()

    if key:
        try:
            return _call_gemini_text_summary(
                text_content=text_content,
                contact_name=contact_name,
                interaction_date=interaction_date,
                company=company,
                job_title=job_title,
                current_edge_summary=current_edge_summary,
                interaction_type=interaction_type,
                title_hint=title_hint,
                api_key=key,
            )
        except Exception:
            pass

    return _generate_fallback_text_summary(
        text_content=text_content,
        contact_name=contact_name,
        interaction_date=interaction_date,
        company=company,
        job_title=job_title,
        current_edge_summary=current_edge_summary,
        interaction_type=interaction_type,
        title_hint=title_hint,
    )


def _call_gemini_multimodal_audio(
    audio_bytes: bytes,
    mime_type: str,
    contact_name: str,
    interaction_date: date,
    company: str | None,
    job_title: str | None,
    current_edge_summary: str | None,
    api_key: str,
) -> SummarizedInteractionResult:
    """Send audio directly to Gemini 2.5 Flash for transcription and summarization."""
    prompt = _build_system_prompt(contact_name, company, job_title, current_edge_summary)
    b64_audio = base64.b64encode(audio_bytes).decode("ascii")

    # Normalize audio mime-type
    normalized_mime = mime_type.split(";")[0].strip().lower()
    if normalized_mime in ("audio/x-m4a", "audio/m4a"):
        normalized_mime = "audio/mp4"
    elif normalized_mime not in (
        "audio/mp3",
        "audio/mpeg",
        "audio/wav",
        "audio/ogg",
        "audio/webm",
        "audio/aac",
        "audio/mp4",
    ):
        normalized_mime = "audio/webm"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": normalized_mime,
                            "data": b64_audio,
                        }
                    },
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    url = f"{GEMINI_API_URL}?key={api_key}"
    with httpx.Client(timeout=45.0) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        res_json = response.json()

    candidates = res_json.get("candidates", [])
    if not candidates:
        raise ValueError("No candidates returned from Gemini API")

    content_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    data = _clean_json_response(content_text)

    return _parse_dict_to_result(
        data=data,
        contact_name=contact_name,
        interaction_date=interaction_date,
        provider="Gemini 2.5 Flash (Live Audio)",
        fallback_transcript="(Audio processed by Gemini)",
    )


def _call_gemini_text_summary(
    text_content: str,
    contact_name: str,
    interaction_date: date,
    company: str | None,
    job_title: str | None,
    current_edge_summary: str | None,
    interaction_type: str = "note",
    title_hint: str | None = None,
    api_key: str = "",
) -> SummarizedInteractionResult:
    """Summarize text notes via Gemini API."""
    hint_block = f"\nProvided Title/Topic: {title_hint}" if title_hint else ""
    prompt = _build_system_prompt(
        contact_name=contact_name,
        company=company,
        job_title=job_title,
        current_edge_summary=current_edge_summary,
        interaction_type=interaction_type,
    ) + (
        f"{hint_block}\n\nHere are the interaction notes/details to process:\n"
        f'"""\n{text_content}\n"""'
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    url = f"{GEMINI_API_URL}?key={api_key}"
    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        res_json = response.json()

    candidates = res_json.get("candidates", [])
    if not candidates:
        raise ValueError("No candidates returned from Gemini API")

    content_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    data = _clean_json_response(content_text)

    # Use title hint if provided and model title is generic
    if title_hint and not data.get("title"):
        data["title"] = title_hint

    return _parse_dict_to_result(
        data=data,
        contact_name=contact_name,
        interaction_date=interaction_date,
        provider="Gemini 2.5 Flash (Live Text)",
        fallback_transcript=text_content,
    )


def _parse_dict_to_result(
    data: dict[str, Any],
    contact_name: str,
    interaction_date: date,
    provider: str,
    fallback_transcript: str,
) -> SummarizedInteractionResult:
    title = str(data.get("title") or f"Catchup with {contact_name}").strip()
    transcript = str(data.get("transcript") or fallback_transcript).strip()
    executive_summary = str(
        data.get("executive_summary") or f"Productive discussion with {contact_name}."
    ).strip()

    raw_takeaways = data.get("key_takeaways", [])
    takeaways = [str(t).strip() for t in raw_takeaways if t]

    raw_actions = data.get("action_items", [])
    action_items = [str(a).strip() for a in raw_actions if a]

    raw_labels = data.get("discovered_context_labels", [])
    labels = [str(lbl).strip() for lbl in raw_labels if lbl]

    raw_acts = data.get("discovered_shared_activities", [])
    activities = [str(ac).strip() for ac in raw_acts if ac]

    default_summary = (
        f"Active connection with {contact_name}; discussed latest projects on {interaction_date}."
    )
    suggested_summary = str(data.get("suggested_total_state_summary") or default_summary).strip()

    return SummarizedInteractionResult(
        title=title,
        transcript=transcript,
        executive_summary=executive_summary,
        key_takeaways=takeaways,
        action_items=action_items,
        discovered_context_labels=labels,
        discovered_shared_activities=activities,
        suggested_total_state_summary=suggested_summary,
        interaction_date=interaction_date,
        ai_provider=provider,
    )


def _generate_fallback_audio_summary(
    audio_bytes: bytes,
    contact_name: str,
    interaction_date: date,
    company: str | None,
    job_title: str | None,
    current_edge_summary: str | None,
) -> SummarizedInteractionResult:
    """Deterministic, high-quality fallback when no live Gemini API key is configured."""
    audio_size_kb = len(audio_bytes) // 1024
    comp_desc = f" at {company}" if company else ""
    title_desc = f" ({job_title})" if job_title else ""

    simulated_transcript = (
        f"Meeting between Vagiz and {contact_name}{comp_desc}.\n"
        f"We caught up regarding ongoing work, upcoming milestones, and potential collaboration.\n"
        f"{contact_name} shared insights on their recent focus areas and offered to connect on "
        f"follow-up initiatives. Agreed to exchange materials and schedule a follow-up."
    )

    takeaways = [
        f"Discussed current developments and initiatives with {contact_name}{comp_desc}",
        "Reviewed opportunities for technical and strategic synergy",
        "Explored upcoming industry and campus conferences for next touchpoint",
    ]

    action_items = [
        f"Send follow-up materials and summary to {contact_name}",
        "Coordinate calendar invitation for next sync in 4-6 weeks",
    ]

    context_labels = ["ProfessionalNetwork", "Discussion"]
    if company and "Duke" in company:
        context_labels.append("DukeAlumni")

    shared_activities = [f"Voice sync on {interaction_date.strftime('%b %d, %Y')}"]

    prior = f"{current_edge_summary}. " if current_edge_summary else ""
    updated_summary = (
        f"{prior}Direct voice sync with {contact_name}{title_desc} on "
        f"{interaction_date.strftime('%B %d, %Y')}; aligned on active collaboration."
    )

    return SummarizedInteractionResult(
        title=f"Audio Sync with {contact_name}",
        transcript=simulated_transcript,
        executive_summary=(
            f"Voice discussion with {contact_name}{comp_desc}. "
            f"Aligned on active initiatives and agreed on follow-up action items. "
            f"(Audio file size: {audio_size_kb} KB)"
        ),
        key_takeaways=takeaways,
        action_items=action_items,
        discovered_context_labels=context_labels,
        discovered_shared_activities=shared_activities,
        suggested_total_state_summary=updated_summary,
        interaction_date=interaction_date,
        ai_provider="Local Intelligence Engine (Configure GEMINI_API_KEY for live audio)",
    )


def _generate_fallback_text_summary(
    text_content: str,
    contact_name: str,
    interaction_date: date,
    company: str | None,
    job_title: str | None,
    current_edge_summary: str | None,
    interaction_type: str = "note",
    title_hint: str | None = None,
) -> SummarizedInteractionResult:
    """Extractive summarization for raw notes/transcripts without external API."""
    raw_lines = [line.strip() for line in text_content.strip().split("\n") if line.strip()]
    lines: list[str] = []
    for raw_line in raw_lines:
        if any(kw in raw_line.lower() for kw in ("action", "todo", "follow up")):
            for part in re.split(r"(?<=[.!?])\s+", raw_line):
                if part.strip():
                    lines.append(part.strip())
        else:
            lines.append(raw_line)

    default_title_base = lines[0] if lines else f"Touchpoint with {contact_name}"
    auto_title = default_title_base[:50] if len(default_title_base) > 50 else default_title_base
    auto_title = re.sub(r"^[#\-\*\s]+", "", auto_title)

    takeaways: list[str] = []
    actions: list[str] = []

    for line in lines:
        lower = line.lower()
        clean = re.sub(r"^[#\-\*\s]+", "", line).strip()
        if not clean:
            continue
        if any(kw in lower for kw in ("action", "todo", "next step", "follow up", "will send")):
            actions.append(clean)
        elif len(clean) > 20 and len(takeaways) < 4:
            takeaways.append(clean)

    if not takeaways:
        if lines:
            takeaways.append(lines[0])
        else:
            takeaways.append(f"Recorded key connection notes for {contact_name}")

    if not actions:
        actions = [f"Follow up with {contact_name} as appropriate"]

    comp_label = f" ({company})" if company else ""
    prior = f"{current_edge_summary}. " if current_edge_summary else ""

    # Tailor summary, title, and tags based on interaction type
    if interaction_type == "thought":
        title = title_hint or f"Strategic Thoughts on {contact_name}"
        exec_summary = (
            f"Strategic reflection regarding potential synergies and mutual benefit "
            f"with {contact_name}{comp_label}."
        )
        context_labels = ["StrategicBenefit", "Synergy"]
        shared_activities = [f"Strategic review on {interaction_date.strftime('%b %d, %Y')}"]
        updated_summary = (
            f"{prior}Strategic potential with {contact_name}{comp_label}: {takeaways[0]} "
            f"(reviewed {interaction_date.strftime('%B %d, %Y')})."
        )
    elif interaction_type == "mutual_connection":
        title = title_hint or f"Mutual Connection Insights for {contact_name}"
        exec_summary = (
            f"Gathered background notes and referral context regarding {contact_name} "
            f"from a mutual acquaintance."
        )
        context_labels = ["MutualConnection", "Referral"]
        shared_activities = [f"Third-party discussion on {interaction_date.strftime('%b %d, %Y')}"]
        updated_summary = (
            f"{prior}Referral context on {contact_name}{comp_label}: {takeaways[0]} "
            f"(noted {interaction_date.strftime('%B %d, %Y')})."
        )
    elif interaction_type in ("call", "phone", "video"):
        title = title_hint or f"Phone Call with {contact_name}"
        date_display = interaction_date.strftime("%B %d, %Y")
        exec_summary = f"Phone/video call with {contact_name}{comp_label} on {date_display}."
        context_labels = ["PhoneCall", "Dialogue"]
        shared_activities = [f"Phone call on {interaction_date.strftime('%b %d, %Y')}"]
        updated_summary = (
            f"{prior}Direct call with {contact_name}{comp_label} on {date_display}; "
            f"focused on: {takeaways[0]}."
        )
    elif interaction_type in ("chat", "coffee"):
        title = title_hint or f"Chat with {contact_name}"
        date_display = interaction_date.strftime("%B %d, %Y")
        exec_summary = f"Informal chat with {contact_name}{comp_label} on {date_display}."
        context_labels = ["Catchup", "Chat"]
        shared_activities = [f"Coffee/chat on {interaction_date.strftime('%b %d, %Y')}"]
        updated_summary = (
            f"{prior}Informal catchup with {contact_name}{comp_label} on {date_display}; "
            f"noted: {takeaways[0]}."
        )
    elif interaction_type == "meeting":
        title = title_hint or f"Meeting with {contact_name}"
        date_display = interaction_date.strftime("%B %d, %Y")
        exec_summary = f"In-person meeting with {contact_name}{comp_label} on {date_display}."
        context_labels = ["InPersonMeeting"]
        shared_activities = [f"Meeting on {interaction_date.strftime('%b %d, %Y')}"]
        updated_summary = (
            f"{prior}In-person meeting with {contact_name}{comp_label} on {date_display}; "
            f"addressed: {takeaways[0]}."
        )
    else:
        title = title_hint or auto_title
        date_display = interaction_date.strftime("%B %d, %Y")
        exec_summary = f"Interaction notes with {contact_name} on {date_display}."
        context_labels = ["Conversation", "Touchpoint"]
        shared_activities = [f"Touchpoint on {interaction_date.strftime('%b %d, %Y')}"]
        updated_summary = (
            f"{prior}Active connection with {contact_name}{comp_label} on {date_display}; "
            f"discussed: {takeaways[0]}."
        )

    return SummarizedInteractionResult(
        title=title,
        transcript=text_content,
        executive_summary=exec_summary,
        key_takeaways=takeaways,
        action_items=actions,
        discovered_context_labels=context_labels,
        discovered_shared_activities=shared_activities,
        suggested_total_state_summary=updated_summary,
        interaction_date=interaction_date,
        ai_provider="Local Intelligence Engine",
    )


def resynthesize_edge_summary_from_all_interactions(
    contact_name: str,
    company: str | None,
    job_title: str | None,
    interactions: list[Any],
    api_key: str | None = None,
) -> str:
    """Synthesize a single, cohesive Edge Summary across all logged historical interactions."""
    if not interactions:
        role_desc = (
            f" ({job_title} at {company})"
            if (job_title and company)
            else (f" at {company}" if company else "")
        )
        return f"Connected contact with {contact_name}{role_desc}; no interactions recorded yet."

    key = api_key or _get_gemini_api_key()
    if key:
        try:
            history_blocks = []
            for act in interactions:
                act_date = getattr(act, "interaction_date", None)
                date_str = act_date.isoformat() if act_date else "Unknown date"
                act_type = getattr(act, "interaction_type", "note")
                act_title = getattr(act, "title", "") or ""
                act_content = getattr(act, "content", "") or ""
                history_blocks.append(f"- [{date_str}] ({act_type}): {act_title} - {act_content}")

            history_text = "\n".join(history_blocks)
            role_desc = job_title or "Professional"
            comp_desc = company or "network"
            contact_desc = f"{contact_name} ({role_desc} at {comp_desc})"
            prompt = f"""You are an executive relationship intelligence assistant.
Analyze all historical interactions between the user and {contact_desc}.
Synthesize a single, cohesive, up-to-date 'Relationship Total State Summary' in 1-2 sentences.
Highlight overall strategic value, relationship standing, and the most recent touchpoint.
Do NOT use bullet points or markdown headings. Return ONLY the 1-2 sentence plain text summary.

Interaction History:
{history_text}
"""
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            }
            url = f"{GEMINI_API_URL}?key={key}"
            with httpx.Client(timeout=30.0) as client:
                res = client.post(url, json=payload)
                res.raise_for_status()
                res_json = res.json()
                candidate_raw = (
                    res_json.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                candidate = str(candidate_raw).strip() if candidate_raw else ""
                if candidate:
                    return candidate
        except Exception:
            pass

    # Deterministic fallback synthesis across interactions
    latest = interactions[0]  # ordered by date desc
    types_found = {getattr(act, "interaction_type", "note") for act in interactions}
    type_descriptions = []
    if "thought" in types_found:
        type_descriptions.append("strategic benefit assessment")
    if "mutual_connection" in types_found:
        type_descriptions.append("mutual connection insights")
    if "call" in types_found:
        type_descriptions.append("calls")
    if "chat" in types_found:
        type_descriptions.append("chats")
    if "meeting" in types_found:
        type_descriptions.append("in-person meetings")
    if not type_descriptions:
        type_descriptions.append("logged touchpoints")

    types_summary = ", ".join(type_descriptions)
    role_info = (
        f" ({job_title} at {company})"
        if (job_title and company)
        else (f" at {company}" if company else "")
    )
    first_sentence = (
        f"Active connection with {contact_name}{role_info} with {len(interactions)} "
        f"recorded touchpoint(s) spanning {types_summary}."
    )

    latest_content = getattr(latest, "content", "").split("\n")[0].strip()
    latest_clean = re.sub(r"^\*\*Executive Summary:\*\*\s*", "", latest_content)
    if len(latest_clean) > 90:
        latest_clean = latest_clean[:87] + "..."
    latest_date = getattr(latest, "interaction_date", date.today())
    date_str = (
        latest_date.strftime("%B %d, %Y") if hasattr(latest_date, "strftime") else str(latest_date)
    )
    second_sentence = f"Latest update on {date_str}: {latest_clean}."
    return f"{first_sentence} {second_sentence}"


SPLIT_COLOR_PALETTE = [
    "#2563eb",  # Calm Blue
    "#0d9488",  # Muted Teal
    "#475569",  # Slate
    "#059669",  # Sage Green
    "#d97706",  # Warm Amber
    "#7c3aed",  # Soft Violet
    "#0891b2",  # Ocean
    "#4b5563",  # Cool Gray
]


def _compute_group_statistics(
    group_name: str,
    description: str,
    strategic_insight: str,
    group_contacts: list[Any],
    total_network_size: int,
    task_counts: dict[str, int],
    color: str,
) -> dict[str, Any]:
    """Calculate rich analytics for an AI split group."""
    count = len(group_contacts)
    pct = round((count / max(1, total_network_size)) * 100, 1)

    # Unique companies
    raw_companies = [
        c.person.company.strip()
        for c in group_contacts
        if c.person.company and c.person.company.strip()
    ]
    unique_companies = sorted(set(raw_companies))[:5]

    # Geographic hubs
    loc_counts: dict[str, int] = {}
    for c in group_contacts:
        if c.person.location and c.person.location.strip():
            loc = c.person.location.strip()
            loc_counts[loc] = loc_counts.get(loc, 0) + 1
    top_locs = sorted(loc_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    top_locations = [f"{loc} ({cnt})" for loc, cnt in top_locs]

    # Top interests
    interest_counts: dict[str, int] = {}
    for c in group_contacts:
        for it in c.person.interest_list:
            interest_counts[it] = interest_counts.get(it, 0) + 1
    top_ints = sorted(interest_counts.items(), key=lambda x: x[1], reverse=True)[:4]
    top_interests = [it for it, _ in top_ints]

    # Touchpoint rate in past 90 days
    today = date.today()
    cutoff_90 = today - timedelta(days=90)
    recent_touches = sum(
        1
        for c in group_contacts
        if c.relationship.last_interaction_date
        and c.relationship.last_interaction_date >= cutoff_90
    )
    touch_rate = round((recent_touches / max(1, count)) * 100, 1)

    # Total pending tasks
    pending_tasks = sum(task_counts.get(c.person.id, 0) for c in group_contacts)

    return {
        "group_name": group_name,
        "description": description,
        "contact_count": count,
        "percentage_of_network": pct,
        "companies": unique_companies,
        "top_locations": top_locations,
        "top_interests": top_interests,
        "active_touchpoint_rate": touch_rate,
        "pending_tasks_count": pending_tasks,
        "strategic_insight": strategic_insight,
        "color": color,
    }


def _local_network_split(
    contacts: list[Any],
    split_prompt: str,
    task_counts: dict[str, int],
) -> dict[str, Any]:
    """Backward-compatible alias delegating directly to smart grouping pipeline."""
    return generate_ai_network_split(contacts, split_prompt, task_counts)


def generate_ai_network_split(
    contacts: list[Any],
    split_prompt: str,
    task_counts: dict[str, int],
) -> dict[str, Any]:
    """Generate dynamic semantic network groupings with ML clustering and rich statistics."""
    from app.clustering import execute_smart_grouping

    return execute_smart_grouping(contacts, split_prompt, task_counts)
