# rule_based_1

Dhelmise / Hide 'n' Sneak rule-based agent, extracted unchanged from the notebook
`rule-based-agent-for-dhelmise.ipynb` (the `%%writefile main.py` cell and the deck list cell).

Run it against the server:

    python clients/agent_client.py --agent agents/rule_based_1/main.py --room r1 --name rb1

Or in-process:

    python scripts/local_match.py --a agents/rule_based_1/main.py --b agents/rule_based_1/main.py --games 50
