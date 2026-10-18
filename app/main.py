"""Main FastAPI application for Inner Circle prototype."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.csv_parser import parse_connections_csv
from app.database import get_db
from app.importer import commit_import, preview_import
from app.migrations_runner import apply_migrations
from app.services import (
    create_contact_with_focal_edge,
    create_relationship,
    delete_person,
    delete_relationship,
    ensure_dev_owner,
    get_owner,
    get_person,
    get_relationship_between,
    list_contacts_connected_to_focal,
    list_disconnected_persons,
    update_person,
    update_relationship,
)

DEV_OWNER_ID = "dev-owner"
BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Run migrations and ensure dev owner on startup
    apply_migrations()
    with get_db() as conn:
        ensure_dev_owner(conn, owner_id=DEV_OWNER_ID)
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
    q: str | None = None,
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
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

        contacts = list_contacts_connected_to_focal(conn, DEV_OWNER_ID, focal_id, search_query=q)
        disconnected = list_disconnected_persons(conn, DEV_OWNER_ID, focal_id)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "focal_person": focal,
            "contacts": contacts,
            "disconnected_persons": disconnected,
            "search_query": q,
            "message": message,
            "error": error,
        },
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
    email: Annotated[str, Form()] = "",
    profile_url: Annotated[str, Form()] = "",
    connected_on: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    parsed_date = _parse_optional_date(connected_on)
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
                connected_on=parsed_date,
                notes=notes,
            )
        except ValueError as ex:
            return RedirectResponse(url=f"/?error={ex}", status_code=303)

    return RedirectResponse(
        url=f"/?message=Added+{person.display_name}+to+your+network", status_code=303
    )


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

    return RedirectResponse(
        url=f"/?message=Reconnected+{person.display_name}+to+your+network", status_code=303
    )


@app.get("/contacts/{person_id}", response_class=HTMLResponse)
async def contact_detail_view(
    request: Request,
    person_id: str,
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    with get_db() as conn:
        owner = get_owner(conn, DEV_OWNER_ID)
        assert owner is not None and owner.focal_person_id is not None
        person = get_person(conn, DEV_OWNER_ID, person_id)
        if not person:
            raise HTTPException(status_code=404, detail="Contact not found")

        rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person.id)
        focal = get_person(conn, DEV_OWNER_ID, owner.focal_person_id)

        # Count total incident edges for delete confirmation
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
            "incident_edge_count": edge_count,
            "message": message,
            "error": error,
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
    email: Annotated[str, Form()] = "",
    profile_url: Annotated[str, Form()] = "",
    connected_on: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    parsed_date = _parse_optional_date(connected_on)
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
            )

            rel = get_relationship_between(conn, DEV_OWNER_ID, owner.focal_person_id, person_id)
            if rel:
                update_relationship(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    relationship_id=rel.id,
                    connected_on=parsed_date,
                    notes=notes,
                )
            elif parsed_date or notes:
                create_relationship(
                    conn=conn,
                    owner_id=DEV_OWNER_ID,
                    person_1_id=owner.focal_person_id,
                    person_2_id=person_id,
                    connected_on=parsed_date,
                    notes=notes,
                )
        except ValueError as ex:
            return RedirectResponse(url=f"/contacts/{person_id}?error={ex}", status_code=303)

    return RedirectResponse(
        url=f"/contacts/{person_id}?message=Changes+saved+successfully", status_code=303
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
        url="/?message=Contact+disconnected+from+your+active+network", status_code=303
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

    return RedirectResponse(
        url=f"/?message=Permanently+deleted+{name}+and+all+their+edges", status_code=303
    )


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
    return RedirectResponse(url=f"/?message={msg}", status_code=303)
