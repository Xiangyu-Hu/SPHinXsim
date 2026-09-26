# Continuum in Lagrangian Framework

## Total Lagrangian Formulation for Solid Mechanics

Considering continuum mechanics in the total Lagrangian framework,
the kinematics and dynamic equations are expressed
in terms of the initial, undeformed reference configuration
$\Omega^0 \subset \boldsymbol{R}^d$ with $d$ denoting the dimension.
A deformation map $\varphi$ between the initial configuration $\Omega^0$ and
current deformed configuration $\Omega = \varphi \left( \Omega^0 \right)$
describes the body deformation at time $t$ as

\begin{equation}
\boldsymbol{r} = \varphi \left( \boldsymbol{r}^0, t \right),
\label{deformation-map}
\end{equation}

where $\boldsymbol{r}^0$ and $\boldsymbol{r}$ are the initial and
current positions of a material point, respectively.
Subsequently, the deformation gradient tensor $\boldsymbol{F}$ is given by

\begin{equation}
\boldsymbol{F} = \nabla^0 \boldsymbol{r} = \nabla^0 \boldsymbol{u} + \boldsymbol{I},
\label{deformation-gradient}
\end{equation}

where $\boldsymbol{u} = \boldsymbol{r} - \boldsymbol{r}^0$ is the displacement,
$\nabla^0 \equiv \frac{\partial}{\partial \boldsymbol{r}^0}$
the gradient operator with respect to the initial configuration
$\Omega^0$ and $\boldsymbol{I} $ the identity matrix.

The conservation equations for mass and momentum in
the total Lagrangian formulation can be expressed as

\begin{equation}
\begin{cases}
\rho = J^{-1}\rho^0 \\
\rho^0 \ddot {\boldsymbol{u}} = \nabla^0 \cdot \boldsymbol{P}^{\operatorname{T}},
\end{cases}
\label{conservation-equations}
\end{equation}

where $\rho^0$ and $\rho$ are the initial and current densities, respectively,
$J = \det(\boldsymbol{F})$,
$\ddot {\boldsymbol{u}}$ the acceleration,
$\boldsymbol{P}$ the first Piola-Kirchhoff stress tensor,
and $\operatorname{T}$ the matrix transposition operator.
While $\boldsymbol{P}$ can be obtained directly by

\begin{equation}
\boldsymbol{P} = \boldsymbol{F}\boldsymbol{S},
\label{piola-stress}
\end{equation}

where $\boldsymbol{S}$ is the second Piola-Kirchhoff stress tensor,
$\boldsymbol{P}$ is obtained by the alternative Kirchhoff-stress approach in this work as

\begin{equation}
\boldsymbol{P} = \boldsymbol{\tau}\boldsymbol{F}^{-\operatorname{T}}.
\label{kirchhoff-stress}
\end{equation}

Here, the Kirchhoff stress $\boldsymbol{\tau}$
is decomposed into volumetric and deviatoric components,
and can be derived form the following strain energy function \cite{simo2006computational}

\begin{equation}
\mathfrak{W}_e = \mathfrak{W}_v \left( J \right) + \mathfrak{W}_s \left(\bar {\boldsymbol{b}} \right).
\label{strain-energy}
\end{equation}

Here, the volume-preserving left Cauchy-Green deformation gradient tensor
$\bar  {\boldsymbol{b}} = J^ {-\frac{2}{d}}  \boldsymbol{b} = \left| \boldsymbol{b} \right|^{ - \frac{1}{d}} \boldsymbol{b}$ with $\boldsymbol{b} = \boldsymbol{F}\boldsymbol{F}^{\operatorname{T}}$.
For neo-Hookean materials,
the volume-dependent strain energy $\mathfrak{W}_v \left( J \right)$
weighted by the bulk modulus $K$ can be expressed as

\begin{equation}
\mathfrak{W}_v \left( J \right) = \frac{1}{2}K\left[ \frac{1}{2}\left( J^2 - 1 \right) - \ln J \right],
\label{volumetric-energy}
\end{equation}

whereas the shear-dependent strain energy $\mathfrak{W}_s \left(\bar  {\boldsymbol{b}} \right)$
weighted by the shear modulus $G$ \cite{yue2015continuum} is given by

\begin{equation}
\mathfrak{W}_s \left( \bar{ \boldsymbol{b}} \right) = \frac{1}{2} G \left( \operatorname{tr} \left( \bar {\boldsymbol{b}} \right) - d \right).
\label{deviatoric-energy}
\end{equation}

Then, the Kirchhoff stress tensor $\boldsymbol{\tau}$ can be derived as

\begin{equation}
\boldsymbol{\tau} = \frac{\partial \mathfrak{W}_e}{\partial \boldsymbol{F} } \boldsymbol{F}^{\operatorname{T}}  
 = \frac{K}{2}\left( J^2 - 1 \right) \boldsymbol{I} + G \operatorname{dev} \left( \bar {\boldsymbol{b}} \right),
\label{Kirchhoff_stress}
\end{equation}

where
\begin{equation}
\operatorname{dev} \left( \bar{ \boldsymbol{b}} \right)
= \bar {\boldsymbol{b}} - \frac{1}{d} \operatorname{tr} \left( \bar{ \boldsymbol{b}} \right) \mathbb{I}
= J^ {-\frac{2}{d}} \left[ \boldsymbol{b} - \frac{1}{d} \operatorname{tr} \left( \boldsymbol{b} \right) \mathbb{I} \right].
\label{deviatoric-stress}
\end{equation}

The deviatoric operator $\operatorname{dev}\left( \bar{ \boldsymbol{b}} \right)$
returns the trace-free part of $\bar{ \boldsymbol{b}}$,
i.e., $\operatorname{tr} \left( \operatorname{dev}\left( \bar{ \boldsymbol{b}} \right) \right)$ is equal to zero.
Note that while the volumetric component of the constitutive Eq. $\eqref{Kirchhoff_stress}$
can be modified depending on the material property
and all counterparts are appropriate for this study,
only the Eq. $\eqref{Kirchhoff_stress}$ is utilized in this study.
