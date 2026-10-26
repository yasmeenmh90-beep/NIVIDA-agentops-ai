from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import projects as svc
from ..db import SessionLocal
from ..models import Incident, Project
from ..schemas import ProjectDetail, ProjectIn, ProjectOut, ProjectUpdate
from ..tools.sandbox import resolve_mode

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_by_ref(ref: str | int) -> Project:
    with SessionLocal() as db:
        p = None
        if str(ref).isdigit():
            p = db.get(Project, int(ref))
        if p is None:
            p = db.scalars(select(Project).where(Project.name == str(ref))).first()
        if p is None:
            raise HTTPException(404, f"project '{ref}' not found")
        return p


def _detail(p: Project) -> ProjectDetail:
    d = ProjectDetail.model_validate(p)
    ctx = svc.context_for(p)
    d.github_token_present = bool(ctx.github_token)
    d.effective_test_command = ctx.test_command
    d.effective_sandbox = resolve_mode(ctx.sandbox_mode, ctx.sandbox_image)
    return d


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(body: ProjectIn):
    """Register a repository. Cloning and dependency install start in the background (poll setup_status)."""
    try:
        svc.validate_name(body.name)
        svc.validate_repo_url(body.repo_url)
        app_path = svc.validate_app_path(body.app_path)
    except svc.ProjectError as e:
        raise HTTPException(422, str(e))
    with SessionLocal() as db:
        if db.scalars(select(Project).where(Project.name == body.name)).first():
            raise HTTPException(409, f"project '{body.name}' already exists")
        p = Project(**body.model_dump(exclude={"github_repo", "app_path"}), app_path=app_path,
                    github_repo=body.github_repo if body.github_repo is not None else svc.github_repo_from_url(body.repo_url))
        db.add(p)
        db.commit()
        pid = p.id
    svc.start_setup(pid)
    return _detail(get_project_by_ref(pid))


@router.get("", response_model=list[ProjectOut])
def list_projects():
    with SessionLocal() as db:
        return db.scalars(select(Project).order_by(Project.id)).all()


@router.get("/{ref}", response_model=ProjectDetail)
def get_project(ref: str):
    return _detail(get_project_by_ref(ref))


@router.patch("/{ref}", response_model=ProjectDetail)
def update_project(ref: str, body: ProjectUpdate):
    p = get_project_by_ref(ref)
    changes = body.model_dump(exclude_unset=True)
    if "app_path" in changes:
        try:
            changes["app_path"] = svc.validate_app_path(changes["app_path"] or "")
        except svc.ProjectError as e:
            raise HTTPException(422, str(e))
    with SessionLocal() as db:
        row = db.get(Project, p.id)
        for k, v in changes.items():
            setattr(row, k, v)
        needs_setup = bool({"default_branch", "app_path", "install_command", "python_env"} & changes.keys())
        if needs_setup:
            row.setup_status = "pending"
        db.commit()
    if needs_setup:
        svc.start_setup(p.id)
    return _detail(get_project_by_ref(p.id))


@router.post("/{ref}/setup", response_model=ProjectDetail)
def rerun_setup(ref: str):
    """Re-clone/sync and reinstall dependencies (e.g. after requirements changed)."""
    p = get_project_by_ref(ref)
    if p.setup_status == "running":
        raise HTTPException(409, "setup already running")
    svc.start_setup(p.id)
    return _detail(get_project_by_ref(p.id))


@router.delete("/{ref}", status_code=204)
def delete_project(ref: str):
    p = get_project_by_ref(ref)
    with SessionLocal() as db:
        if db.scalars(select(Incident.id).where(Incident.project_id == p.id)).first():
            raise HTTPException(409, "project has incidents; it cannot be deleted")
        db.delete(db.get(Project, p.id))
        db.commit()
