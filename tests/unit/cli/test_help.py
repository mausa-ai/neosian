"""The top-level help is grouped by audience (DESIGN §30) and ends with
the agents' door."""

from typer.testing import CliRunner

from neosian._cli.main import app


def test_the_help_names_the_four_audiences_and_the_agent_door() -> None:
    result = CliRunner().invoke(app, ["--help"], env={"NO_COLOR": "1"})
    assert result.exit_code == 0
    for panel in ("Talk", "Operate", "Connect an agent", "Learn"):
        assert panel in result.output
    assert "agents: neosian docs cli --json" in result.output
    # Every verb sits in a panel, in the audience order.
    order = [result.output.index(p) for p in ("Talk", "Operate", "Connect", "Learn")]
    assert order == sorted(order)
    assert result.output.index("status") > result.output.index("Operate")
