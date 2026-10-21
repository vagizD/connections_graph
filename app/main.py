"""Main FastAPI application for Inner Circle prototype."""

from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.ai_summarizer import (
    generate_ai_network_split,
    process_audio_interaction,
    process_text_interaction,
)
from app.config import (
    TIERS,
    resolve_prototype_mode,
    set_global_prototype_mode,
)
from app.csv_parser import parse_connections_csv
from app.database import get_db
from app.importer import ImportCommitResult, commit_import, preview_import
from app.migrations_runner import apply_migrations
from app.models import ContactFilterCriteria, ContactView
from app.services import (
    backfill_tasks_from_existing_interactions,
    clear_active_network_split,
    count_pending_tasks,
    create_contact_with_focal_edge,
    create_relationship,
    create_task,
    delete_interaction,
    delete_person,
    delete_relationship,
    delete_task,
    ensure_dev_owner,
    filter_and_group_contacts,
    get_active_network_split,
    get_filter_facet_options,
    get_grouped_split_view,
    get_network_graph_payload,
    get_owner,
    get_person,
    get_relationship_between,
    list_contacts_connected_to_focal,
    list_disconnected_persons,
    list_interactions_for_relationship,
    list_tasks,
    log_interaction_with_edge_synthesis,
    record_summarized_interaction,
    reset_network_for_owner,
    resynthesize_relationship_summary,
    save_network_split,
    seed_sample_tasks_for_test_contacts,
    toggle_task_status,
    update_person,
    update_relationship,
)

DEV_OWNER_ID = "dev-owner"
BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Run migrations and ensure dev owner on startup
    apply_migrations()
    tier = resolve_prototype_mode()
    with get_db() as conn:
        ensure_dev_owner(conn, owner_id=DEV_OWNER_ID)
        if tier.has_tasks:
            backfill_tasks_from_existing_interactions(conn, owner_id=DEV_OWNER_ID)
            if count_pending_tasks(conn, DEV_OWNER_ID) < 5:
                seed_sample_tasks_for_test_contacts(conn, owner_id=DEV_OWNER_ID)
    yield


app = FastAPI(title="Inner Circle", lifespan=lifespan)

# Mount static files and setup templates
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _parse_optional_date(val: str | None) -> date | None:
    if not val or not val.strip():
        return None
    try:
        return datetime.strptime(val.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@app.get("/", response_class=HTMLResponse)
async def index_view(
    request: Request,
    mode: str | None = None,
    tab: str | None = None,
    view: str | None = None,
    q: str | None = None,
    company: str | None = None,
    location: str | None = None,
    education: str | None = None,
    interest: str | None = None,
    context_label: str | None = None,
    last_interaction: str | None = None,
    group_by: str = "none",
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    tier = resolve_prototype_mode(mode)
    has_tasks = tier.has_tasks
    has_graph = tier.has_graph

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        if not owner or not owner.focal_person_id:
            _, focal = ensure_dev_owner(conn, DEV_OWNER_ID)
            focal_id = focal.id
        else:
            focal_id = owner.focal_person_id
            focal_person_obj = get_person(conn, DEV_OWNER_ID, focal_id)
            assert focal_person_obj is not None
            focal = focal_person_obj

        criteria = ContactFilterCriteria(
            search_query=q,
            company=company,
            location=location,
            education=education,
            interest=interest,
            context_label=context_label,
            last_interaction=last_interaction,
        )

        filter_result = filter_and_group_contacts(
            conn=conn,
            owner_id=DEV_OWNER_ID,
            focal_person_id=focal_id,
            criteria=criteria,
            group_dimension=group_by,
        )

        facets = get_filter_facet_options(conn, DEV_OWNER_ID, focal_id)
        disconnected = list_disconnected_persons(conn, DEV_OWNER_ID, focal_id)
        all_contacts = list_contacts_connected_to_focal(conn, DEV_OWNER_ID, focal_id)

        total_contact_count = len(all_contacts)
        unique_companies = {
            c.person.company.strip()
            for c in all_contacts
            if c.person.company and c.person.company.strip()
        }
        unique_companies_count = len(unique_companies)
        today = date.today()
        recent_touches_count = sum(
            1
            for c in all_contacts
            if c.relationship.last_interaction_date
            and (today - c.relationship.last_interaction_date).days <= 90
        )
        recent_contacts = all_contacts[:4]

        has_filter_param = any(
            [
                q,
                company,
                location,
                education,
                interest,
                context_label,
                last_interaction,
                group_by != "none",
                view is not None,
            ]
        )

        if has_tasks:
            all_tasks = list_tasks(conn, DEV_OWNER_ID)
            pending_tasks_count = sum(1 for t in all_tasks if t.status == "pending")
            completed_tasks_count = sum(1 for t in all_tasks if t.status == "completed")
        else:
            all_tasks = []
            pending_tasks_count = 0
            completed_tasks_count = 0

        if tab == "tasks" and has_tasks:
            active_tab = "tasks"
        elif tab == "contacts":
            active_tab = "contacts"
        elif tab == "profile":
            active_tab = "profile"
        elif has_filter_param:
            active_tab = "contacts"
        else:
            active_tab = "profile"

        active_split = get_active_network_split(conn, DEV_OWNER_ID)
        split_groups = None
        if active_split and group_by == "none":
            visible_contacts: list[ContactView] = []
            seen_c_ids: set[str] = set()
            for grp in filter_result.groups:
                for c in grp.contacts:
                    if c.person.id not in seen_c_ids:
                        seen_c_ids.add(c.person.id)
                        visible_contacts.append(c)
            split_groups = get_grouped_split_view(active_split, visible_contacts)

        effective_view_mode = (view or "list") if has_graph else "list"
        if has_graph:
            graph_payload = get_network_graph_payload(
                conn, DEV_OWNER_ID, focal, filter_result, active_split=active_split
            )
        else:
            graph_payload = {"nodes": [], "edges": [], "clusters": []}

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_tab": active_tab,
            "view_mode": effective_view_mode,
            "focal_person": focal,
            "filter_result": filter_result,
            "facets": facets,
            "disconnected_persons": disconnected,
            "criteria": criteria,
            "group_by": group_by,
            "search_query": q,
            "message": message,
            "error": error,
            "total_contact_count": total_contact_count,
            "unique_companies_count": unique_companies_count,
            "recent_touches_count": recent_touches_count,
            "recent_contacts": recent_contacts,
            "tasks": all_tasks,
            "pending_tasks_count": pending_tasks_count,
            "completed_tasks_count": completed_tasks_count,
            "all_contacts": all_contacts,
            "graph_json": json.dumps(graph_payload),
            "graph_data": graph_payload,
            "active_split": active_split,
            "split_groups": split_groups,
            "prototype_mode": tier.mode,
            "prototype_tier": tier,
            "has_tasks": has_tasks,
            "has_graph": has_graph,
            "all_prototype_tiers": TIERS,
        },
    )


@app.post("/splits/create")
async def create_split_endpoint(
    prompt: Annotated[str, Form(...)],
    redirect_to: Annotated[str, Form()] = "/?tab=contacts",
) -> RedirectResponse:
    """Generate an AI-driven network split based on user prompt, compute analytics, and apply it."""
    clean_prompt = prompt.strip()
    if not clean_prompt:
        return RedirectResponse(
            url="/?tab=contacts&error=Please+provide+a+split+description", status_code=303
        )

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner and owner.focal_person_id
        contacts = list_contacts_connected_to_focal(conn, DEV_OWNER_ID, owner.focal_person_id)
        if not contacts:
            return RedirectResponse(
                url="/?tab=contacts&error=No+contacts+to+split", status_code=303
            )

        pending_tasks = conn.execute(
            """
            SELECT contact_person_id, COUNT(*) as c
            FROM tasks
            WHERE owner_id = ? AND status = 'pending' AND contact_person_id IS NOT NULL
            GROUP BY contact_person_id;
            """,
            (DEV_OWNER_ID,),
        ).fetchall()
        task_counts = {str(r["contact_person_id"]): int(r["c"]) for r in pending_tasks}

        split_dict = generate_ai_network_split(contacts, clean_prompt, task_counts)
        save_network_split(conn, DEV_OWNER_ID, clean_prompt, split_dict)

    return RedirectResponse(
        url="/?tab=contacts&message=AI+Network+Split+applied+successfully", status_code=303
    )


@app.post("/splits/clear")
async def clear_split_endpoint(
    redirect_to: Annotated[str, Form()] = "/?tab=contacts",
) -> RedirectResponse:
    """Clear active network split, returning to standard view."""
    with get_db() as conn:
        clear_active_network_split(conn, DEV_OWNER_ID)
    return RedirectResponse(url="/?tab=contacts&message=Active+split+cleared", status_code=303)


@app.get("/api/split")
async def active_split_api() -> JSONResponse:
    """Return JSON details of currently active network split and its group statistics."""
    with get_db() as conn:
        active_split = get_active_network_split(conn, DEV_OWNER_ID)
        if not active_split:
            return JSONResponse({"active": False, "split": None})

        return JSONResponse(
            {
                "active": True,
                "split": {
                    "id": active_split.id,
                    "title": active_split.title,
                    "prompt": active_split.prompt,
                    "groups": [dataclasses.asdict(g) for g in active_split.groups],
                    "total_contacts": sum(g.contact_count for g in active_split.groups),
                },
            }
        )


@app.get("/api/graph")
async def network_graph_api(
    q: str | None = None,
    company: str | None = None,
    location: str | None = None,
    education: str | None = None,
    interest: str | None = None,
    context_label: str | None = None,
    last_interaction: str | None = None,
    group_by: str = "none",
) -> JSONResponse:
    """JSON API delivering nodes, edges, clusters, and planar coordinates for the graph."""
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        if not owner or not owner.focal_person_id:
            _, focal = ensure_dev_owner(conn, DEV_OWNER_ID)
            focal_id = focal.id
        else:
            focal_id = owner.focal_person_id
            focal_person_obj = get_person(conn, DEV_OWNER_ID, focal_id)
            assert focal_person_obj is not None
            focal = focal_person_obj

        criteria = ContactFilterCriteria(
            search_query=q,
            company=company,
            location=location,
            education=education,
            interest=interest,
            context_label=context_label,
            last_interaction=last_interaction,
        )

        filter_result = filter_and_group_contacts(
            conn=conn,
            owner_id=DEV_OWNER_ID,
            focal_person_id=focal_id,
            criteria=criteria,
            group_dimension=group_by,
        )

        active_split = get_active_network_split(conn, DEV_OWNER_ID)
        payload = get_network_graph_payload(
            conn, DEV_OWNER_ID, focal, filter_result, active_split=active_split
        )
        return JSONResponse(payload)


@app.post("/profile/edit")
async def edit_profile_endpoint(
    first_name: Annotated[str, Form(...)],
    last_name: Annotated[str, Form()] = "",
    job_title: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    education: Annotated[str, Form()] = "",
    interests: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
) -> RedirectResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        focal_id = owner.focal_person_id
        try:
            update_person(
                conn=conn,
                owner_id=DEV_OWNER_ID,
                person_id=focal_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                company=company,
                job_title=job_title,
                location=location,
                education=education,
                interests=interests,
            )
        except ValueError as ex:
            return RedirectResponse(url=f"/?tab=profile&error={ex}", status_code=303)

    return RedirectResponse(
        url="/?tab=profile&message=Profile+updated+successfully",
        status_code=303,
    )


@app.get("/contacts/new", response_class=HTMLResponse)
async def new_contact_form(request: Request) -> HTMLResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        focal = get_person(conn, DEV_OWNER_ID, owner.focal_person_id)
        disconnected = list_disconnected_persons(conn, DEV_OWNER_ID, owner.focal_person_id)

    return templates.TemplateResponse(
        request,
        "contact_form.html",
        {
            "focal_person": focal,
            "disconnected_persons": disconnected,
        },
    )


@app.post("/contacts/new")
async def create_new_contact(
    first_name: Annotated[str, Form(...)],
    last_name: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    job_title: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    education: Annotated[str, Form()] = "",
    interests: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    profile_url: Annotated[str, Form()] = "",
    connected_on: Annotated[str, Form()] = "",
    context_labels: Annotated[str, Form()] = "",
    shared_activities: Annotated[str, Form()] = "",
    last_interaction_date: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    parsed_conn_date = _parse_optional_date(connected_on)
    parsed_last_date = _parse_optional_date(last_interaction_date)
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        try:
            person, _ = create_contact_with_focal_edge(
                conn=conn,
                owner_id=DEV_OWNER_ID,
                focal_person_id=owner.focal_person_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                profile_url=profile_url,
                company=company,
                job_title=job_title,
                location=location,
                education=education,
                interests=interests,
                connected_on=parsed_conn_date,
                notes=notes,
                context_labels=context_labels,
                shared_activities=shared_activities,
                last_interaction_date=parsed_last_date,
                summary=summary,
            )
        except ValueError as ex:
            return RedirectResponse(url=f"/?tab=contacts&error={ex}", status_code=303)

    msg = f"Added+{person.display_name}+to+your+network"
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


@app.post("/contacts/reconnect-existing")
async def reconnect_existing(
    person_id: Annotated[str, Form(...)],
) -> RedirectResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        person = get_person(conn, DEV_OWNER_ID, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        create_relationship(
            conn=conn,
            owner_id=DEV_OWNER_ID,
            person_1_id=owner.focal_person_id,
            person_2_id=person_id,
            connected_on=date.today(),
            notes="Reconnected to focal person",
        )

    msg = f"Reconnected+{person.display_name}+to+your+network"
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


@app.get("/contacts/{person_id}", response_class=HTMLResponse)
async def contact_detail_view(
    request: Request,
    person_id: str,
    mode: str | None = None,
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    tier = resolve_prototype_mode(mode)
    has_tasks = tier.has_tasks
    has_graph = tier.has_graph

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        person = get_person(conn, DEV_OWNER_ID, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")

        rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person.id)
        focal = get_person(conn, DEV_OWNER_ID, owner.focal_person_id)

        interactions = list_interactions_for_relationship(conn, DEV_OWNER_ID, rel.id) if rel else []
        if has_tasks:
            contact_tasks = list_tasks(conn, DEV_OWNER_ID, contact_person_id=person.id)
            pending_tasks_count = count_pending_tasks(conn, DEV_OWNER_ID)
        else:
            contact_tasks = []
            pending_tasks_count = 0

        cursor = conn.execute(
            "SELECT COUNT(*) as c FROM relationships "
            "WHERE owner_id = ? AND (person_a_id = ? OR person_b_id = ?);",
            (DEV_OWNER_ID, person.id, person.id),
        )
        edge_count = int(cursor.fetchone()["c"])

    return templates.TemplateResponse(
        request,
        "contact_detail.html",
        {
            "focal_person": focal,
            "person": person,
            "relationship": rel,
            "interactions": interactions,
            "tasks": contact_tasks,
            "pending_tasks_count": pending_tasks_count,
            "incident_edge_count": edge_count,
            "message": message,
            "error": error,
            "prototype_mode": tier.mode,
            "prototype_tier": tier,
            "has_tasks": has_tasks,
            "has_graph": has_graph,
            "all_prototype_tiers": TIERS,
        },
    )


@app.post("/contacts/{person_id}/edit")
async def edit_contact(
    person_id: str,
    first_name: Annotated[str, Form(...)],
    last_name: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    job_title: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    education: Annotated[str, Form()] = "",
    interests: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    profile_url: Annotated[str, Form()] = "",
    connected_on: Annotated[str, Form()] = "",
    context_labels: Annotated[str, Form()] = "",
    shared_activities: Annotated[str, Form()] = "",
    last_interaction_date: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    parsed_conn_date = _parse_optional_date(connected_on)
    parsed_last_date = _parse_optional_date(last_interaction_date)
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None

        try:
            update_person(
                conn=conn,
                owner_id=DEV_OWNER_ID,
                person_id=person_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                profile_url=profile_url,
                company=company,
                job_title=job_title,
                location=location,
                education=education,
                interests=interests,
            )

            rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
            effective_summary = (
                summary.strip() if summary and summary.strip() else (rel.summary if rel else None)
            )
            if rel:
                update_relationship(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    relationship_id=rel.id,
                    connected_on=parsed_conn_date,
                    notes=notes,
                    context_labels=context_labels,
                    shared_activities=shared_activities,
                    last_interaction_date=parsed_last_date,
                    summary=effective_summary,
                )
            elif (
                parsed_conn_date
                or notes
                or context_labels
                or shared_activities
                or parsed_last_date
                or effective_summary
            ):
                create_relationship(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    person_1_id=owner.focal_person_id,
                    person_2_id=person_id,
                    connected_on=parsed_conn_date,
                    notes=notes,
                    context_labels=context_labels,
                    shared_activities=shared_activities,
                    last_interaction_date=parsed_last_date,
                    summary=effective_summary,
                )
        except ValueError as ex:
            return RedirectResponse(url=f"/contacts/{person_id}?error={ex}", status_code=303)

    return RedirectResponse(
        url=f"/contacts/{person_id}?message=Changes+saved+successfully", status_code=303
    )


@app.post("/contacts/{person_id}/interactions")
async def add_interaction(
    person_id: str,
    interaction_date: Annotated[str, Form(...)],
    interaction_type: Annotated[str, Form()] = "note",
    title: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    audio_file: Annotated[UploadFile | None, File()] = None,
    api_key: Annotated[str, Form()] = "",
) -> RedirectResponse:
    idate = _parse_optional_date(interaction_date) or date.today()
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        person = get_person(conn, DEV_OWNER_ID, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
        if not rel:
            raise HTTPException(status_code=404, detail="Relationship not found")

        audio_bytes: bytes | None = None
        mime_type = "audio/webm"
        if audio_file and audio_file.filename:
            raw_audio = await audio_file.read()
            if raw_audio:
                audio_bytes = raw_audio
                mime_type = audio_file.content_type or "audio/webm"

        try:
            if audio_bytes:
                summary_result = process_audio_interaction(
                    audio_bytes=audio_bytes,
                    mime_type=mime_type,
                    contact_name=person.display_name,
                    interaction_date=idate,
                    company=person.company,
                    job_title=person.job_title,
                    current_edge_summary=rel.summary,
                    title_hint=title.strip() or None,
                    api_key=api_key.strip() or None,
                )
                if content.strip():
                    new_transcript = f"{summary_result.transcript}\n\nNotes: {content.strip()}"
                    summary_result = dataclasses.replace(summary_result, transcript=new_transcript)
                record_summarized_interaction(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    relationship_id=rel.id,
                    summary_result=summary_result,
                    auto_update_edge_state=True,
                )
            else:
                if not content.strip() and not title.strip():
                    return RedirectResponse(
                        url=f"/contacts/{person_id}?error=Please+enter+notes+or+record+audio",
                        status_code=303,
                    )
                log_interaction_with_edge_synthesis(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    relationship_id=rel.id,
                    contact_person=person,
                    interaction_date=idate,
                    interaction_type=interaction_type,
                    title=title,
                    content=content.strip() or title.strip(),
                    auto_update_edge_state=True,
                    api_key=api_key.strip() or None,
                )
        except ValueError as ex:
            return RedirectResponse(url=f"/contacts/{person_id}?error={ex}", status_code=303)

    return RedirectResponse(
        url=f"/contacts/{person_id}?message=Interaction+added",
        status_code=303,
    )


@app.post("/contacts/{person_id}/summary/resynthesize")
async def resynthesize_summary_endpoint(
    person_id: str,
    api_key: Annotated[str, Form()] = "",
) -> RedirectResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        person = get_person(conn, DEV_OWNER_ID, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")
        rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
        if not rel:
            raise HTTPException(status_code=404, detail="Relationship not found")

        resynthesize_relationship_summary(
            conn=conn,
            owner_id=DEV_OWNER_ID,
            relationship_id=rel.id,
            contact_person=person,
            api_key=api_key.strip() or None,
        )

    return RedirectResponse(
        url=f"/contacts/{person_id}?message=Summary+updated",
        status_code=303,
    )


@app.post("/contacts/{person_id}/interactions/audio")
async def summarize_audio_interaction_endpoint(
    person_id: str,
    audio_file: Annotated[UploadFile | None, File()] = None,
    raw_text: Annotated[str, Form()] = "",
    interaction_date: Annotated[str, Form()] = "",
    auto_update_edge_state: Annotated[bool, Form()] = True,
    api_key: Annotated[str, Form()] = "",
) -> RedirectResponse:
    idate = _parse_optional_date(interaction_date) or date.today()

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        person = get_person(conn, DEV_OWNER_ID, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")

        rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
        if not rel:
            raise HTTPException(status_code=404, detail="Relationship not found")

        audio_bytes: bytes | None = None
        mime_type = "audio/webm"
        if audio_file and audio_file.filename:
            content = await audio_file.read()
            if content:
                audio_bytes = content
                mime_type = audio_file.content_type or "audio/webm"

        if audio_bytes:
            summary_result = process_audio_interaction(
                audio_bytes=audio_bytes,
                mime_type=mime_type,
                contact_name=person.display_name,
                interaction_date=idate,
                company=person.company,
                job_title=person.job_title,
                current_edge_summary=rel.summary,
                api_key=api_key.strip() or None,
            )
        elif raw_text.strip():
            summary_result = process_text_interaction(
                text_content=raw_text.strip(),
                contact_name=person.display_name,
                interaction_date=idate,
                company=person.company,
                job_title=person.job_title,
                current_edge_summary=rel.summary,
                api_key=api_key.strip() or None,
            )
        else:
            return RedirectResponse(
                url=(
                    f"/contacts/{person_id}?"
                    "error=Please+record+audio,+upload+an+audio+file,+or+enter+notes"
                ),
                status_code=303,
            )

        record_summarized_interaction(
            conn=conn,
            owner_id=DEV_OWNER_ID,
            relationship_id=rel.id,
            summary_result=summary_result,
            auto_update_edge_state=auto_update_edge_state,
        )

    msg = "AI+conversation+summary+recorded+and+relationship+state+updated"
    return RedirectResponse(url=f"/contacts/{person_id}?message={msg}", status_code=303)


@app.post("/contacts/{person_id}/interactions/{interaction_id}/delete")
async def remove_interaction(
    person_id: str,
    interaction_id: str,
) -> RedirectResponse:
    with get_db() as conn:
        delete_interaction(conn, DEV_OWNER_ID, interaction_id)
        owner = get_owner(conn, DEV_OWNER_ID)
        if owner and owner.focal_person_id:
            person = get_person(conn, DEV_OWNER_ID, person_id)
            rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
            if person and rel:
                resynthesize_relationship_summary(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    relationship_id=rel.id,
                    contact_person=person,
                )

    return RedirectResponse(
        url=f"/contacts/{person_id}?message=Interaction+removed+and+AI+summary+recalculated",
        status_code=303,
    )


@app.post("/contacts/{person_id}/disconnect")
async def disconnect_contact(person_id: str) -> RedirectResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
        if rel:
            delete_relationship(conn, DEV_OWNER_ID, rel.id)

    return RedirectResponse(
        url="/?tab=contacts&message=Contact+disconnected+from+your+active+network", status_code=303
    )


@app.post("/contacts/{person_id}/reconnect")
async def reconnect_contact(person_id: str) -> RedirectResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        existing_rel = get_relationship_between(
            conn, DEV_OWNER_ID, owner.focal_person_id, person_id
        )
        if not existing_rel:
            create_relationship(
                conn=conn,
                owner_id=DEV_OWNER_ID,
                person_1_id=owner.focal_person_id,
                person_2_id=person_id,
                connected_on=date.today(),
                notes=None,
            )

    return RedirectResponse(
        url=f"/contacts/{person_id}?message=Contact+reconnected+to+your+active+network",
        status_code=303,
    )


@app.post("/contacts/{person_id}/delete")
async def delete_contact_endpoint(person_id: str) -> RedirectResponse:
    with get_db() as conn:
        p = get_person(conn, DEV_OWNER_ID, person_id)
        name = p.display_name if p else "Contact"
        delete_person(conn, DEV_OWNER_ID, person_id)

    msg = f"Permanently+deleted+{name}+and+all+their+edges"
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


@app.get("/import", response_class=HTMLResponse)
async def import_upload_view(request: Request, error: str | None = None) -> HTMLResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        focal = (
            get_person(conn, DEV_OWNER_ID, owner.focal_person_id)
            if owner and owner.focal_person_id
            else None
        )

    return templates.TemplateResponse(
        request,
        "import.html",
        {"focal_person": focal, "error": error},
    )


def _load_and_commit_fixture(fixture_name: str) -> ImportCommitResult:
    fixture_file = BASE_DIR.parent / "data" / "fixtures" / fixture_name
    if not fixture_file.exists():
        raise HTTPException(status_code=404, detail=f"Fixture {fixture_name} not found")

    content = fixture_file.read_bytes()
    parse_result = parse_connections_csv(content)

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        preview = preview_import(conn, DEV_OWNER_ID, parse_result.rows)
        if not preview.can_commit:
            raise HTTPException(
                status_code=400,
                detail=f"Fixture {fixture_name} contained conflicts",
            )
        commit_res = commit_import(conn, DEV_OWNER_ID, preview)
        seed_sample_tasks_for_test_contacts(conn, DEV_OWNER_ID)
        return commit_res


@app.post("/import/test-table")
async def import_test_table_instant(
    dataset: Annotated[str, Form()] = "",
) -> RedirectResponse:
    if dataset == "100":
        res = _load_and_commit_fixture("duke_international_100.csv")
        title = "100 Duke international student connections"
    elif dataset == "5":
        res = _load_and_commit_fixture("duke_classmates_5.csv")
        title = "5 Duke classmates"
    else:
        res = _load_and_commit_fixture("fictional_contacts_10.csv")
        title = "contacts"

    msg = (
        f"Test+table+imported:+{res.created_count}+{title}+created,"
        f"+{res.updated_count}+updated,+{res.unchanged_count}+already+connected"
    )
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


@app.post("/import/test-100")
async def import_test_100_endpoint() -> RedirectResponse:
    res = _load_and_commit_fixture("duke_international_100.csv")
    msg = (
        f"Imported+{res.created_count}+connections+(Duke+international+student+network):"
        f"+{res.unchanged_count}+already+present"
    )
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


@app.post("/import/test-5")
async def import_test_5_endpoint() -> RedirectResponse:
    res = _load_and_commit_fixture("duke_classmates_5.csv")
    msg = (
        f"Imported+{res.created_count}+classmates+(focused+test+case):"
        f"+{res.unchanged_count}+already+present"
    )
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


@app.post("/network/reset")
async def reset_network_endpoint() -> RedirectResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        deleted = reset_network_for_owner(conn, DEV_OWNER_ID, owner.focal_person_id)

    msg = f"Network+reset:+removed+{deleted}+contacts+and+all+edges"
    return RedirectResponse(url=f"/?tab=profile&message={msg}", status_code=303)


@app.post("/import/demo")
async def import_demo_fixture(request: Request) -> HTMLResponse:
    demo_file = BASE_DIR.parent / "data" / "fixtures" / "fictional_contacts_10.csv"
    if not demo_file.exists():
        raise HTTPException(status_code=404, detail="Demo fixture not found")

    content = demo_file.read_bytes()
    parse_result = parse_connections_csv(content)

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        focal = get_person(conn, DEV_OWNER_ID, owner.focal_person_id)
        preview = preview_import(conn, DEV_OWNER_ID, parse_result.rows)

    payload_b64 = base64.b64encode(content).decode("ascii")
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "focal_person": focal,
            "preview": preview,
            "csv_payload": payload_b64,
        },
    )


@app.post("/import/preview")
async def import_preview_view(
    request: Request,
    file: Annotated[UploadFile, File()],
) -> HTMLResponse:
    content = await file.read()
    parse_result = parse_connections_csv(content)

    if parse_result.errors and not parse_result.rows:
        err_msg = "; ".join(f"Line {e.row_number}: {e.message}" for e in parse_result.errors)
        return templates.TemplateResponse(
            request,
            "import.html",
            {"error": f"Failed to parse CSV: {err_msg}"},
        )

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        focal = get_person(conn, DEV_OWNER_ID, owner.focal_person_id)
        preview = preview_import(conn, DEV_OWNER_ID, parse_result.rows)

    payload_b64 = base64.b64encode(content).decode("ascii")
    return templates.TemplateResponse(
        request,
        "import_preview.html",
        {
            "focal_person": focal,
            "preview": preview,
            "csv_payload": payload_b64,
        },
    )


@app.post("/import/commit")
async def import_commit_endpoint(
    csv_payload: Annotated[str, Form(...)],
) -> RedirectResponse:
    content = base64.b64decode(csv_payload.encode("ascii"))
    parse_result = parse_connections_csv(content)

    with get_db() as conn:
        preview = preview_import(conn, DEV_OWNER_ID, parse_result.rows)
        if not preview.can_commit:
            return RedirectResponse(
                url="/import?error=Import+contained+unresolved+conflicts+or+errors",
                status_code=303,
            )

        res = commit_import(conn, DEV_OWNER_ID, preview)

    msg = (
        f"Import+successful:+{res.created_count}+created,"
        f"+{res.updated_count}+updated,+{res.unchanged_count}+unchanged"
    )
    return RedirectResponse(url=f"/?tab=contacts&message={msg}", status_code=303)


# ============================================================================
# Task Routes
# ============================================================================


@app.post("/tasks/new")
async def create_task_endpoint(
    title: Annotated[str, Form(...)],
    deadline: Annotated[str, Form()] = "",
    contact_person_id: Annotated[str, Form()] = "",
    redirect_to: Annotated[str, Form()] = "",
) -> RedirectResponse:
    target_url = redirect_to.strip() or "/?tab=tasks"
    tier = resolve_prototype_mode()
    sep = "&" if "?" in target_url else "?"
    if not tier.has_tasks:
        return RedirectResponse(
            url=f"{target_url}{sep}error=Tasks+are+disabled+in+{tier.name}",
            status_code=303,
        )

    clean_title = title.strip()
    if not clean_title:
        return RedirectResponse(
            url=f"{target_url}{sep}error=Task+title+cannot+be+empty",
            status_code=303,
        )

    parsed_deadline = _parse_optional_date(deadline) or (date.today() + timedelta(days=7))
    contact_id = contact_person_id.strip() or None

    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        rel_id = None
        if contact_id:
            rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, contact_id)
            if rel:
                rel_id = rel.id

        create_task(
            conn=conn,
            owner_id=DEV_OWNER_ID,
            title=clean_title,
            deadline=parsed_deadline,
            contact_person_id=contact_id,
            relationship_id=rel_id,
        )

    return RedirectResponse(url=f"{target_url}{sep}message=Task+added", status_code=303)


@app.post("/tasks/{task_id}/toggle")
async def toggle_task_endpoint(
    task_id: str,
    redirect_to: Annotated[str, Form()] = "",
) -> RedirectResponse:
    target_url = redirect_to.strip() or "/?tab=tasks"
    tier = resolve_prototype_mode()
    sep = "&" if "?" in target_url else "?"
    if not tier.has_tasks:
        return RedirectResponse(
            url=f"{target_url}{sep}error=Tasks+are+disabled+in+{tier.name}",
            status_code=303,
        )

    with get_db() as conn:
        toggle_task_status(conn, DEV_OWNER_ID, task_id)

    return RedirectResponse(url=f"{target_url}{sep}message=Task+updated", status_code=303)


@app.post("/tasks/{task_id}/delete")
async def delete_task_endpoint(
    task_id: str,
    redirect_to: Annotated[str, Form()] = "",
) -> RedirectResponse:
    target_url = redirect_to.strip() or "/?tab=tasks"
    tier = resolve_prototype_mode()
    sep = "&" if "?" in target_url else "?"
    if not tier.has_tasks:
        return RedirectResponse(
            url=f"{target_url}{sep}error=Tasks+are+disabled+in+{tier.name}",
            status_code=303,
        )

    with get_db() as conn:
        delete_task(conn, DEV_OWNER_ID, task_id)

    return RedirectResponse(url=f"{target_url}{sep}message=Task+deleted", status_code=303)


# ============================================================================
# Mode Switcher Route (for interactive live demo switching)
# ============================================================================


@app.get("/set-mode/{mode_id}")
@app.post("/set-mode/{mode_id}")
async def set_mode_endpoint(
    mode_id: str,
    redirect_to: str = "/",
) -> RedirectResponse:
    target_mode = mode_id.strip().upper()
    tier = resolve_prototype_mode(target_mode)
    set_global_prototype_mode(tier.mode)
    sep = "&" if "?" in redirect_to else "?"
    msg = f"Switched+to+{tier.name}:+{tier.label}".replace(" ", "+")
    return RedirectResponse(url=f"{redirect_to}{sep}message={msg}", status_code=303)


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Inner Circle Prototype Server")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["A", "B", "C", "a", "b", "c"],
        default=resolve_prototype_mode().mode,
        help="Prototype mode: A (Contacts & Grouping), B (+ Tasks), C (+ Graph)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host IP")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--reload", action="store_true", help="Enable reload")
    args = parser.parse_args()

    set_global_prototype_mode(args.mode)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
