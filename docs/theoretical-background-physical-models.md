# Continuum in Lagrangian Framework

## Total Lagrangian Formulation for Solid Mechanics

Considering continuum mechanics in the total Lagrangian framework,
the kinematics and dynamic equations are expressed
in terms of the initial, undeformed reference configuration
$\Omega^0 \subset \bm{R}^d$ with $d$ denoting the dimension.
A deformation map $\varphi$ between the initial configuration $\Omega^0$ and
current deformed configuration $\Omega = \varphi \left( \Omega^0 \right)$
describes the body deformation at time $t$ as

$$
\begin{equation}
\bm{r} = \varphi \left( \bm{r}^0, t \right),
\end{equation}
$$

where $\bm{r}^0$ and $\bm{r}$ are the initial and
current positions of a material point, respectively.
Subsequently, the deformation gradient tensor $\bm{F}$ is given by

$$
\begin{equation}
\bm{F} = \nabla^0 \bm{r} = \nabla^0 \bm{u} + \bm{I},
\end{equation}
$$

where $\bm{u} = \bm{r} - \bm{r}^0$ is the displacement,
$\nabla^0 \equiv \frac{\partial}{\partial \bm{r}^0}$
the gradient operator with respect to the initial configuration
$\Omega^0$ and $\bm{I} $ the identity matrix.

The conservation equations for mass and momentum in
the total Lagrangian formulation can be expressed as

$$
\begin{equation}
\begin{cases}
\rho = J^{-1}\rho^0 \\
\rho^0 \ddot {\bm{u}} = \nabla^0 \cdot \bm{P}^{\operatorname{T}},
\end{cases}
\end{equation}
$$

where $\rho^0$ and $\rho$ are the initial and current densities, respectively,
$J = \det(\bm{F})$,
$\ddot {\bm{u}}$ the acceleration,
$\bm{P}$ the first Piola-Kirchhoff stress tensor,
and $\operatorname{T}$ the matrix transposition operator.
While $\bm{P}$ can be obtained directly by

$$
\begin{equation}
\bm{P} = \bm{F}\bm{S},
\end{equation}
$$

where $\bm{S}$ is the second Piola-Kirchhoff stress tensor,
$\bm{P}$ is obtained by the alternative Kirchhoff-stress approach in this work as

$$
\begin{equation}
\bm{P} = \bm{\tau}\bm{F}^{-\operatorname{T}}.
\end{equation}
$$

Here, the Kirchhoff stress $\bm{\tau}$
is decomposed into volumetric and deviatoric components,
and can be derived form the following strain energy function \cite{simo2006computational}

$$
\begin{equation}
\mathfrak{W}_e = \mathfrak{W}_v \left( J \right) + \mathfrak{W}_s \left(\bar {\bm{b}} \right).
\end{equation}
$$

Here, the volume-preserving left Cauchy-Green deformation gradient tensor
$\bar  {\bm{b}} = J^ {-\frac{2}{d}}  \bm{b} = \left| \bm{b} \right|^{ - \frac{1}{d}} \bm{b}$ with $\bm{b} = \bm{F}\bm{F}^{\operatorname{T}}$.
For neo-Hookean materials,
the volume-dependent strain energy $\mathfrak{W}_v \left( J \right)$
weighted by the bulk modulus $K$ can be expressed as

$$
\begin{equation}
\mathfrak{W}_v \left( J \right) = \frac{1}{2}K\left[ \frac{1}{2}\left( J^2 - 1 \right) - \ln J \right],
\end{equation}
$$

whereas the shear-dependent strain energy $\mathfrak{W}_s \left(\bar  {\bm{b}} \right)$
weighted by the shear modulus $G$ \cite{yue2015continuum} is given by

$$
\begin{equation}
\mathfrak{W}_s \left( \bar{ \bm{b}} \right) = \frac{1}{2} G \left( \operatorname{tr} \left( \bar {\bm{b}} \right) - d \right).
\end{equation}
$$

Then, the Kirchhoff stress tensor $\bm{\tau}$ can be derived as

$$
\begin{equation}
\bm{\tau} = \frac{\partial \mathfrak{W}_e}{\partial \bm{F} } \bm{F}^{\operatorname{T}}  
 = \frac{K}{2}\left( J^2 - 1 \right) \bm{I} + G \operatorname{dev} \left( \bar {\bm{b}} \right),
\end{equation}
$$

where
$$
\begin{equation}
\operatorname{dev} \left( \bar{ \bm{b}} \right)
= \bar {\bm{b}} - \frac{1}{d} \operatorname{tr} \left( \bar{ \bm{b}} \right) \mathbb{I}
= J^ {-\frac{2}{d}} \left[ \bm{b} - \frac{1}{d} \operatorname{tr} \left( \bm{b} \right) \mathbb{I} \right].
\end{equation}
$$

The deviatoric operator $\operatorname{dev}\left( \bar{ \bm{b}} \right)$
returns the trace-free part of $\bar{ \bm{b}}$,
i.e., $\operatorname{tr} \left( \operatorname{dev}\left( \bar{ \bm{b}} \right) \right)$ is equal to zero.
Note that while the volumetric component of the constitutive Eq. \eqref{Kirchhoff_stress}
can be modified depending on the material property
and all counterparts are appropriate for this study,
only the Eq. \eqref{Kirchhoff_stress} is utilized in this study.
