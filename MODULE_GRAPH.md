# cli-anything-qcad — Module Dependency Graph

```mermaid
flowchart LR

    subgraph backends[Backends]
        dwg_converter[dwg_converter]
    end

    subgraph core[Core]
        categories[categories]
        planner[planner]
        session[session]
    end

    subgraph engines[Engines]
        clone_terminal_wires[clone_terminal_wires]
        cloud_clone[cloud_clone]
        delete_clouded_entities[delete_clouded_entities]
        extra_ops[extra_ops]
        geometry_ops[geometry_ops]
        text_based_clone[text_based_clone]
        text_value[text_value]
    end

    subgraph utils[Utils]
        cloud_overlay[cloud_overlay]
        drawing_profile[drawing_profile]
        dxf_entity_index[dxf_entity_index]
        layer_fix[layer_fix]
        pdf_parser[pdf_parser]
        render[render]
        terminal_positions[terminal_positions]
        visual_verifier[visual_verifier]
        visual_verify[visual_verify]
    end

    subgraph pipelines[Pipelines]
        markup_pipeline[markup_pipeline]
    end

    subgraph other[Entry Point]
        qcad_cli[qcad_cli]
    end

    cloud_clone --> delete_clouded_entities
    cloud_clone --> layer_fix
    cloud_clone --> terminal_positions
    cloud_overlay --> dwg_converter
    cloud_overlay --> dxf_entity_index
    cloud_overlay --> planner
    dwg_converter --> layer_fix
    markup_pipeline --> clone_terminal_wires
    markup_pipeline --> cloud_clone
    markup_pipeline --> cloud_overlay
    markup_pipeline --> delete_clouded_entities
    markup_pipeline --> drawing_profile
    markup_pipeline --> dwg_converter
    markup_pipeline --> extra_ops
    markup_pipeline --> geometry_ops
    markup_pipeline --> planner
    markup_pipeline --> text_based_clone
    markup_pipeline --> text_value
    markup_pipeline --> visual_verify
    planner --> categories
    planner --> cloud_overlay
    planner --> dxf_entity_index
    planner --> pdf_parser
    qcad_cli --> categories
    qcad_cli --> dwg_converter
    qcad_cli --> markup_pipeline
    qcad_cli --> pdf_parser
    qcad_cli --> render
    qcad_cli --> session
    qcad_cli --> visual_verifier
    terminal_positions --> drawing_profile
    text_based_clone --> layer_fix
    text_based_clone --> terminal_positions
    text_value --> drawing_profile
    text_value --> dxf_entity_index
    visual_verify --> dwg_converter

    classDef backends fill:#e1f5fe,stroke:#0288d1
    classDef core fill:#f3e5f5,stroke:#7b1fa2
    classDef engines fill:#fff3e0,stroke:#e65100
    classDef utils fill:#e8f5e9,stroke:#2e7d32
    classDef pipelines fill:#fce4ec,stroke:#c62828
    classDef other fill:#f5f5f5,stroke:#616161

    class dwg_converter backends
    class categories core
    class planner core
    class session core
    class clone_terminal_wires engines
    class cloud_clone engines
    class delete_clouded_entities engines
    class extra_ops engines
    class geometry_ops engines
    class text_based_clone engines
    class text_value engines
    class cloud_overlay utils
    class drawing_profile utils
    class dxf_entity_index utils
    class layer_fix utils
    class pdf_parser utils
    class render utils
    class terminal_positions utils
    class visual_verifier utils
    class visual_verify utils
    class markup_pipeline pipelines
    class qcad_cli other
```

**Note:** `archive/` at the repo root contains superseded prototypes — the old `vlm_automation/` directory (149 files), 5 retired engine variants, 3 unused backends, and orphaned scripts. None are imported by the active package.