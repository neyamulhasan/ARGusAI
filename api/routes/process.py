"""Process endpoint to trigger alignment in background."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from api.job_store import job_store
from api.models import ProcessRequest, ProcessResponse
from modules.pipeline.runner import PipelineRunner

logger = logging.getLogger(__name__)
router = APIRouter(tags=["process"])


def _run_pipeline_job(job_id: str, evalue: float, program: str) -> None:
    """Background task: run full pipeline and store results."""

    record = job_store.get(job_id)
    if record is None:
        return

    logger.info("Job %s started (program=%s, evalue=%s)", job_id, program, evalue)
    job_store.update(job_id, status="running", stage="alignment")
    try:
        def _update_stage(stage: str) -> None:
            job_store.update(job_id, stage=stage)
            logger.info("Job %s stage -> %s", job_id, stage)

        runner = PipelineRunner()
        result = runner.run(
            run_id=job_id,
            fasta_path=record.fasta_path,
            fasta_filename=record.filename,
            evalue=evalue,
            program=program,
            stage_callback=_update_stage,
        )

        job_store.update(
            job_id,
            status="complete",
            stage="complete",
            hits=result.hits,
            report=result.report,
            text_summary=result.text_summary,
        )
        logger.info("Job %s completed with %d hits", job_id, len(result.hits))
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        job_store.update(job_id, status="error", stage="alignment", error=str(exc))


@router.post("/process/{job_id}", response_model=ProcessResponse)
async def process_job(job_id: str, request: ProcessRequest, tasks: BackgroundTasks) -> ProcessResponse:
    """Queue background RAG pipeline processing for uploaded FASTA."""

    record = job_store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if request.program not in {"blastx", "blastp"}:
        raise HTTPException(status_code=400, detail="Program must be 'blastx' or 'blastp'")

    if record.status == "running":
        raise HTTPException(status_code=409, detail="Job is already running")

    tasks.add_task(_run_pipeline_job, job_id, request.evalue, request.program)
    job_store.update(job_id, status="pending", stage="queued", error=None)
    logger.info("Job %s queued", job_id)
    return ProcessResponse(job_id=job_id, status="pending", message="Pipeline job queued")
