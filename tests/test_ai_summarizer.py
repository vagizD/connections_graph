"""Tests for audio transcription and AI conversation summarizer."""

from datetime import date
from pathlib import Path

from app.ai_summarizer import (
    SummarizedInteractionResult,
    process_audio_interaction,
    process_text_interaction,
)
from app.database import get_db
from app.migrations_runner import apply_migrations
from app.services import (
    _merge_comma_separated,
    create_contact_with_focal_edge,
    ensure_dev_owner,
    get_relationship_by_id,
    list_interactions_for_relationship,
    log_interaction_with_edge_synthesis,
    record_summarized_interaction,
    resynthesize_relationship_summary,
)


def test_process_text_interaction_fallback() -> None:
    notes = """
    Met with Liam Walker at Duke West Union coffee shop.
    Discussed progress on HackDuke 2025 planning and robotics team sponsorship.
    Action item: send draft prospectus to Liam by Friday.
    Action item: schedule call with Fuqua VC fellows.
    """
    result = process_text_interaction(
        text_content=notes,
        contact_name="Liam Walker",
        interaction_date=date(2026, 10, 20),
        company="Duke University",
        job_title="Robotics Lead",
        current_edge_summary="Collaborating on robotics lab",
    )

    assert isinstance(result, SummarizedInteractionResult)
    assert "Liam" in result.title or "Met" in result.title
    assert len(result.key_takeaways) >= 1
    assert len(result.action_items) >= 1
    assert "Liam Walker" in result.suggested_total_state_summary
    assert "Collaborating on robotics lab" in result.suggested_total_state_summary
    assert result.interaction_date == date(2026, 10, 20)


def test_process_audio_interaction_fallback() -> None:
    fake_audio = b"\x00\x01\x02\x03" * 1024  # 4 KB dummy audio
    result = process_audio_interaction(
        audio_bytes=fake_audio,
        mime_type="audio/webm",
        contact_name="Sophia Ramirez",
        interaction_date=date(2026, 10, 20),
        company="Duke Capital Partners",
        job_title="Managing Director",
        current_edge_summary="Initial coffee chat",
    )

    assert isinstance(result, SummarizedInteractionResult)
    assert result.title == "Audio Sync with Sophia Ramirez"
    assert "Sophia Ramirez" in result.transcript
    assert len(result.key_takeaways) >= 2
    assert len(result.action_items) >= 2
    assert "DukeAlumni" in result.discovered_context_labels
    assert "Sophia Ramirez" in result.suggested_total_state_summary
    assert result.interaction_date == date(2026, 10, 20)


def test_merge_comma_separated_helpers() -> None:
    res = _merge_comma_separated("Duke, Tech", ["Tech", "VentureCapital", "#Robotics"])
    assert res == "Duke, Tech, VentureCapital, Robotics"

    res_empty = _merge_comma_separated(None, ["Classmate"])
    assert res_empty == "Classmate"


def test_record_summarized_interaction_and_edge_state_update(tmp_path: Path) -> None:
    db_file = tmp_path / "test_ai.db"
    apply_migrations(db_file)
    with get_db(db_file) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="owner-ai")
        person, rel = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Kenji",
            last_name="Sato",
            company="DeepMind",
            job_title="Research Scientist",
            context_labels="AIResearch",
            shared_activities="Duke Pratt Lab",
            summary="Collaborating on AI papers",
            last_interaction_date=date(2026, 9, 15),
        )

        fake_audio = b"fake-audio-bytes" * 100
        summary_result = process_audio_interaction(
            audio_bytes=fake_audio,
            mime_type="audio/wav",
            contact_name="Kenji Sato",
            interaction_date=date(2026, 10, 20),
            company="DeepMind",
            job_title="Research Scientist",
            current_edge_summary=rel.summary,
        )

        interaction, updated_rel = record_summarized_interaction(
            conn=conn,
            owner_id=owner.id,
            relationship_id=rel.id,
            summary_result=summary_result,
            auto_update_edge_state=True,
        )

        assert interaction.relationship_id == rel.id
        assert interaction.interaction_type == "audio_transcript"
        assert "Executive Summary:" in interaction.content
        assert "Action Items & Next Steps:" in interaction.content

        # Verify edge total state was updated
        assert updated_rel.last_interaction_date == date(2026, 10, 20)
        assert updated_rel.summary is not None
        assert "Direct voice sync with Kenji Sato" in updated_rel.summary
        assert "Collaborating on AI papers" in updated_rel.summary

        # Context labels merged
        assert updated_rel.context_labels is not None
        assert "AIResearch" in updated_rel.context_labels
        assert "ProfessionalNetwork" in updated_rel.context_labels

        # Relationship in DB reflects updates
        refreshed_rel = get_relationship_by_id(conn, owner.id, rel.id)
        assert refreshed_rel is not None
        assert refreshed_rel.last_interaction_date == date(2026, 10, 20)
        assert refreshed_rel.summary == updated_rel.summary

        # Interactions list returns new item
        interactions = list_interactions_for_relationship(conn, owner.id, rel.id)
        assert len(interactions) == 1
        assert interactions[0].id == interaction.id


def test_interaction_types_and_edge_synthesis(tmp_path: Path) -> None:
    db_file = tmp_path / "test_interaction_types.db"
    apply_migrations(db_file)
    with get_db(db_file) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="owner-types")
        person, rel = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Marcus",
            last_name="Vance",
            company="Apex Capital",
            job_title="Partner",
            context_labels="VentureCapital",
            notes="Met at Durham Angel Summit",
        )
        assert rel.summary is None

        # 1. Log a strategic thought: how one could be beneficial
        thought_interaction, rel_after_thought = log_interaction_with_edge_synthesis(
            conn=conn,
            owner_id=owner.id,
            relationship_id=rel.id,
            contact_person=person,
            interaction_date=date(2026, 10, 1),
            interaction_type="thought",
            title="How Marcus could advise our student fund",
            content=(
                "Marcus has deep ties to Southeast LPs and angel syndicates. "
                "Could serve on our advisory committee and help review pitch decks."
            ),
        )
        assert thought_interaction.interaction_type == "thought"
        assert rel_after_thought.summary is not None
        assert "Marcus" in rel_after_thought.summary
        assert "Strategic potential with Marcus Vance" in rel_after_thought.summary
        assert "Southeast LPs" in rel_after_thought.summary

        # 2. Log talking to someone who knows the connection (third-party context)
        mutual_interaction, rel_after_mutual = log_interaction_with_edge_synthesis(
            conn=conn,
            owner_id=owner.id,
            relationship_id=rel.id,
            contact_person=person,
            interaction_date=date(2026, 10, 8),
            interaction_type="mutual_connection",
            title="Chatted with Elena about Marcus",
            content=(
                "Elena mentioned Marcus is actively mentoring student founders "
                "and interested in co-investing in AI startups."
            ),
        )
        assert mutual_interaction.interaction_type == "mutual_connection"
        assert rel_after_mutual.summary is not None
        assert "Referral context on Marcus Vance" in rel_after_mutual.summary
        assert "mentoring student founders" in rel_after_mutual.summary

        # 3. Log a phone call
        call_interaction, rel_after_call = log_interaction_with_edge_synthesis(
            conn=conn,
            owner_id=owner.id,
            relationship_id=rel.id,
            contact_person=person,
            interaction_date=date(2026, 10, 15),
            interaction_type="call",
            title="Introductory Strategy Call",
            content=(
                "Had a 30-min call. Marcus agreed to attend our demo day "
                "and review 2 pitch decks next month."
            ),
        )
        assert call_interaction.interaction_type == "call"
        assert rel_after_call.summary is not None
        assert "Direct call with Marcus Vance" in rel_after_call.summary
        assert "demo day" in rel_after_call.summary
        assert rel_after_call.last_interaction_date == date(2026, 10, 15)

        # 4. Verify full re-synthesis across all 3 interactions
        resynthesized_rel = resynthesize_relationship_summary(
            conn=conn,
            owner_id=owner.id,
            relationship_id=rel.id,
            contact_person=person,
        )
        assert resynthesized_rel.summary is not None
        assert "Marcus Vance" in resynthesized_rel.summary
        assert "3 recorded touchpoint(s)" in resynthesized_rel.summary
        assert "strategic benefit assessment" in resynthesized_rel.summary
        assert "mutual connection insights" in resynthesized_rel.summary
        assert "calls" in resynthesized_rel.summary
