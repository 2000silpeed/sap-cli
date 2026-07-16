from sap_cli.ontology.schema import ScreenOntology, SapField, TransactionOntology
from sap_cli.ontology.store import OntologyStore
from sap_cli.ui_map.schema import UiComponent, UiState
from sap_cli.ui_map.semantic import MapSemanticReviewer
from sap_cli.ui_map.search import UiMapSearch
from sap_cli.ui_map.schema import MapManifest, SemanticLink, UiMap


def test_local_ontology_bridge_proposes_value_free_screen_and_field_links(tmp_path):
    store = OntologyStore(tmp_path / "knowledge")
    ontology = TransactionOntology(tcode="ZMAP")
    ontology.add_screen(
        ScreenOntology(
            transaction="ZMAP",
            screen_number=100,
            fields=[SapField(id="wnd[0]/usr/ctxtMATNR", name="MATNR")],
        )
    )
    store.save_transaction(ontology)
    state = UiState(
        state_id="state-100",
        tcode="ZMAP",
        screen_number=100,
        components=[
            UiComponent(
                logical_id="material",
                sap_id="wnd[0]/usr/ctxtMATNR",
                type="GuiCTextField",
                changeable=True,
                # A business value must not become a semantic candidate.
                text="SECRET-MATERIAL-100",
            )
        ],
    )

    links = MapSemanticReviewer(store).propose(state)

    assert {(item.predicate, item.object_id) for item in links} >= {
        ("maps_to_ontology_screen", "ontology_screen:ZMAP:100"),
        ("maps_to_ontology_field", "ontology_field:ZMAP:100:wnd[0]/usr/ctxtMATNR"),
        ("has_ui_role", "ui_role:selection"),
    }
    assert "SECRET-MATERIAL-100" not in "".join(item.model_dump_json() for item in links)


def test_confirmed_semantic_role_is_searchable_but_proposed_role_is_not():
    state = UiState(state_id="state-1", tcode="ZMAP")
    ui_map = UiMap(
        manifest=MapManifest(map_id="semantic", tcode="ZMAP"),
        states={state.state_id: state},
        semantic_links=[
            SemanticLink(
                link_id="proposed", subject_kind="state", subject_id="state-1",
                predicate="has_ui_role", object_id="ui_role:selection",
            ),
            SemanticLink(
                link_id="confirmed", subject_kind="state", subject_id="state-1",
                predicate="has_ui_role", object_id="ui_role:overview", status="confirmed",
            ),
        ],
    )

    index = UiMapSearch(ui_map)

    assert [item.entity_id for item in index.search("overview")] == ["state-1"]
    assert index.search("selection") == []
