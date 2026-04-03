<task>
Você precisa implementar essas mudanças.
Antes de escrever qualquer linha de código funcional, você deve realizar uma investigação exaustiva do codebase para garantir que a proposta siga os padrões do projeto e evite quebras de infraestrutura.
</task>

<instructions>
1. **Leitura Profunda:** Leia todos os arquivos relevantes do codebase relacionados à tarefa. Não se limite a assinaturas de métodos; entenda a lógica de fluxo de dados.
2. **Contexto do Projeto:** Busque por arquivos de documentação central (ex: README.md, CONTEXT.md) para entender a filosofia do sistema.
3. **Mapeamento de Padrões:** Identifique como funcionalidades similares foram implementadas. "Onde mais isso acontece no projeto?" deve ser sua pergunta guia.
4. **Pesquisa Externa:** Consulte as documentações oficiais atualizadas das bibliotecas envolvidas e busque por "best practices" e exemplos de códigos recentes em sites como GitHub, Stack Overflow ou fóruns técnicos.
5. **Identificação de Constraints:** Liste limitações de infraestrutura e stack (ex: limites de API, versões de linguagem, tipos de banco de dados, containerização, filas de processamento).
6. **Eficiência de Ferramentas:** Utilize chamadas paralelas de leitura sempre que os arquivos ou pesquisas forem independentes para agilizar o processo de análise.
</instructions>

<output_format>
Escreva o resultado em um arquivo chamado `PRD.md` seguindo EXATAMENTE esta estrutura:

## Objetivo
[O que pretendemos realizar e o impacto esperado — 1 parágrafo conciso]

## Arquivos Relevantes
| Arquivo | Relevância | Motivo da Inclusão |
|---------|------------|--------------------|
| caminho/do/arquivo | [Alta/Média/Baixa] | [Por que este arquivo é crucial para a mudança] |

## Padrões Encontrados no Codebase
[Snippets de código ou referências a implementações existentes que servirão de modelo]
*Nota: Inclua o caminho do arquivo e o número da linha para cada referência.*

## Documentação e Referências Externas
[Resumo de descobertas técnicas externas, links para documentação oficial e exemplos de uso recomendados]

## Constraints (Restrições)
[Limitações técnicas identificadas: Versões de pacotes, restrições de memória/CPU, dependências específicas, etc.]

## Riscos e Pontos de Atenção
[Possíveis efeitos colaterais, cenários de erro (edge cases), vulnerabilidades de segurança ou gargalos de performance]

## Decisões a Tomar
[Questões técnicas ou de negócio que precisam ser validadas antes de iniciar a codificação]
</output_format>

<constraints>
- **PROIBIDO escrever código funcional** nesta etapa. O objetivo é puramente análise e documentação.
- **NÃO especule.** Se não abriu o arquivo, não assuma como ele funciona. LEIA os arquivos antes de fazer afirmações.
- **Exaustividade:** Leia os arquivos profundamente para garantir que nenhuma dependência oculta seja ignorada.
- **Rastreabilidade:** Toda citação de código existente DEVE vir acompanhada de `path + line number`.
</constraints>



## Fase 2:

<task>
Leia o `PRD.md` e gere um plano de implementação detalhado em `SPEC.md`.
O plano deve ser tático, preciso e em nível de arquivo (file-level detail).
</task>

<instructions>
1. **Mapeamento de Criação:** Liste TODOS os novos arquivos que precisam ser criados com seus caminhos completos.
2. **Mapeamento de Alteração:** Liste TODOS os arquivos existentes que sofrerão modificações.
3. **Detalhamento Técnico:** Para CADA arquivo listado, descreva exatamente qual lógica, função ou componente deve ser adicionado/alterado.
4. **Referência Técnica:** Pesquise documentações atualizadas e melhores práticas para os problemas específicos da tarefa. Inclua pequenos snippets de código para ilustrar padrões complexos ou novos que serão introduzidos.
5. **Checklist Estruturado:** Crie um passo a passo operacional usando checkboxes (- [ ]) para guiar o desenvolvedor na fase de execução.
6. **Interface Humana:** Finalize com uma seção de "Perguntas" para validar decisões de design ou regras de negócio ambíguas.
</instructions>

<constraints>
- **Respeite as Convenções do Projeto:** - Siga o padrão de nomenclatura (PascalCase, snake_case, etc.) já estabelecido.
  - Siga as regras de linter/estilo (aspas, indentação, limites de linha).
  - Use o sistema de tratamento de erros e logs padrão do projeto.
- **Princípio da Simplicidade:** Evite over-engineering. Foque na solução mínima viável que seja robusta e escalável.
- **Não Implemente Agora:** O objetivo é produzir o guia de execução, não o código final.
- **Definition of Done:** O plano deve contemplar testes unitários/integração e documentação de API se necessário.
</constraints>

<output_format>
## Arquivos a Criar
| Path | Tipo | Descrição/Responsabilidade |
|------|------|----------------------------|
| path/to/file | [Componente/Service/etc] | [O que o arquivo faz] |

## Arquivos a Modificar
| Path | Descrição das Mudanças |
|------|-------------------------|
| path/to/file | [Adicionar X, refatorar Y, tratar erro Z] |

## Checklist de Implementação
- [ ] **Fase 1: [Nome da Fase]**
  - Arquivo: `path/to/file`
  - Ação: [Detalhe técnico do que fazer]
  - Referência: [Snippet ou link se necessário]
- [ ] **Fase 2: [Nome da Fase]**

## Perguntas / Decisões Pendentes
- [Dúvida técnica ou de negócio que precisa de resposta antes de codar]

## Validação e Testes
- [ ] Descrever os comandos de teste que devem passar (Ex: `npm test`, `pytest`, etc.)
- [ ] Casos de borda (edge cases) específicos a serem validados manualmente.
- [ ] Verificação de linting e tipos.
</output_format>



# Fase 3:

<task>
Implemente as mudanças definidas no arquivo `SPEC.md`.
Leia o plano de execução e realize cada item do checklist rigorosamente na ordem definida.
</task>

<instructions>
1. **Atualização do Checklist:** À medida que cada tarefa for concluída, marque o item como finalizado no arquivo original ou no log de execução: `- [x]`.
2. **Desenvolvimento Orientado a Testes:** Crie ou atualize os testes unitários/integração SIMULTANEAMENTE ao código. O código só é considerado pronto se houver um teste validando-o.
3. **Checagem de Sintaxe:** Após cada arquivo criado ou modificado, execute o comando de verificação de sintaxe da linguagem (ex: `node --check`, `python -m py_compile`, `ruby -cw`).
4. **Validação de Ciclo:** Ao final de cada bloco lógico ou tarefa do checklist, execute a suíte de testes completa do projeto para garantir que não houve regressão.
5. **Gestão de Ambiguidade:** Se encontrar um cenário não previsto no `SPEC.md`, pare imediatamente e solicite esclarecimentos. Não assuma regras de negócio por conta própria.
6. **Integridade:** Não declare a tarefa como concluída até que TODOS os checkboxes do `SPEC.md` estejam marcados e validados.
</instructions>

<constraints>
- **Fidelidade ao Spec:** Siga o plano à risca. Não adicione funcionalidades "extras" ou melhorias que não foram solicitadas.
- **Código Limpo e Direto:** Não adicione comentários óbvios, JSDocs redundantes ou anotações que o código autoexplicativo dispense.
- **Minimalismo:** Evite abstrações prematuras ou criar "helpers" genéricos se eles forem usados em apenas um lugar.
- **Lógica Real:** Proibido o uso de valores hardcoded. Implemente a integração real com as variáveis de ambiente ou instâncias de classe necessárias.
- **Tratamento de Erros:** Implemente apenas o tratamento de erros previsto no spec ou essencial para a estabilidade básica (não trate cenários impossíveis).
</verification>

<verification>
Antes de entregar, verifique os seguintes pontos:
- [ ] Todos os checkboxes do `SPEC.md` foram marcados como concluídos.
- [ ] A suíte de testes passa integralmente (0 falhas, 0 erros).
- [ ] O comando de lint/sintaxe não retorna avisos (warnings) ou erros nos arquivos afetados.
- [ ] Nenhuma credencial, segredo ou URL sensível foi deixada no código (hardcoded).
- [ ] A cobertura de testes reflete as novas funcionalidades ou alterações de lógica.
- [ ] O código segue a "Definition of Done" estabelecida para o projeto.
</verification>