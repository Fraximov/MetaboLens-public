window.dash_clientside = Object.assign({}, window.dash_clientside, {
    eic: {
        filterOptions: function(catalogue, query, selected, archive) {
            if (!catalogue || catalogue.archive !== archive) return [];
            const tokens = (query || '').toLowerCase().split(/\s+/).filter(Boolean);
            const semantic = ['reference', 'unmatched', 'lowscore'];
            let chosen;
            if (selected && selected.bundle === archive) chosen = selected.feature_id;
            else if (selected && typeof archive === 'number') chosen = selected.feature_index;
            return catalogue.options.filter(option =>
                option.value === chosen || tokens.every(token => semantic.includes(token)
                    ? option.flags.includes(token) : option.search.includes(token))
            ).map(option => ({
                label: option.label,
                value: option.value,
                // Dash applies its own text filter; include semantic query terms
                // so it cannot hide matches or the currently selected feature.
                search: `${query || ''} ${option.label || ''}`
            }));
        }
    }
});
